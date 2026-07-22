import base64
import hashlib
import json
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.serializers.json import DjangoJSONEncoder

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class BackupEngine:
    """
    Base engine for signed app/model backup packages.

    Package structure:
      manifest.json
      signature.txt          (base64 PSS-RSA signature of manifest.json)
      apps/
        <app_label>/
          <model_name>.json
    """

    BACKUP_FORMAT = "toto.shared_apps.backup"
    BACKUP_VERSION = "1.0"

    def __init__(self, platform, apps_to_sync=None, vault_password=None, sign=True):
        self.platform = platform
        self.apps_to_sync = list(apps_to_sync or getattr(settings, "APPS_TO_SYNC", []))
        self.vault_password = (
            vault_password
            or getattr(settings, "BACKUP_VAULT_PASSWORD", "")
            or getattr(settings, "SSO_VAULT_PASSWORD", "")
        )
        self.sign = sign

    def _validate_apps_for_create(self):
        if not self.apps_to_sync:
            raise ImproperlyConfigured("apps_to_sync must be provided for backup creation.")
        allowed = set(getattr(settings, "APPS_TO_SYNC", []))
        invalid = set(self.apps_to_sync) - allowed
        if invalid:
            raise ImproperlyConfigured(
                f"Apps not in APPS_TO_SYNC: {', '.join(sorted(invalid))}"
            )

    # ---------------------------------------------------------
    # Model discovery
    # ---------------------------------------------------------

    def iter_models(self):
        """Yields backup-safe models from selected apps, ordered by BACKUP_MODEL_ORDER."""
        discovered = []
        for app_label in self.apps_to_sync:
            for model in apps.get_app_config(app_label).get_models():
                if self.is_backup_model(model):
                    discovered.append(model)

        order = getattr(settings, "BACKUP_MODEL_ORDER", [])
        if order:
            index = {label: i for i, label in enumerate(order)}
            discovered.sort(key=lambda m: index.get(m._meta.label, 10_000))

        yield from discovered

    def is_backup_model(self, model):
        excluded = set(getattr(settings, "BACKUP_EXCLUDED_MODELS", []))
        if model._meta.label in excluded:
            return False
        return self._model_has_field(model, "uid")

    def _model_has_field(self, model, field_name):
        try:
            model._meta.get_field(field_name)
            return True
        except Exception:
            return False

    # ---------------------------------------------------------
    # Manifest
    # ---------------------------------------------------------

    def build_manifest(self):
        return {
            "format": self.BACKUP_FORMAT,
            "version": self.BACKUP_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_platform": {
                "id": self.platform.id,
                "site_name": self.platform.site_name,
                "domain": self.platform.domain,
            },
            "apps": list(self.apps_to_sync),
            "models": [],
            "hashes": {},
        }

    def load_manifest(self, tmpdir):
        p = Path(tmpdir) / "manifest.json"
        if not p.exists():
            raise ImproperlyConfigured("Backup is missing manifest.json.")
        return json.loads(p.read_text(encoding="utf-8"))

    def validate_manifest(self, manifest):
        if manifest.get("format") != self.BACKUP_FORMAT:
            raise ImproperlyConfigured("Invalid backup format.")
        if manifest.get("version") != self.BACKUP_VERSION:
            raise ImproperlyConfigured("Unsupported backup version.")
        allowed = set(getattr(settings, "APPS_TO_SYNC", []))
        invalid = set(manifest.get("apps", [])) - allowed
        if invalid:
            raise ImproperlyConfigured(
                f"Backup contains disallowed apps: {', '.join(sorted(invalid))}"
            )

    # ---------------------------------------------------------
    # Serialization
    # ---------------------------------------------------------

    def serialize_model(self, model):
        return {
            "app": model._meta.app_label,
            "model": model._meta.model_name,
            "label": model._meta.label,
            "objects": [self.serialize_object(obj) for obj in model.objects.all()],
        }

    def serialize_object(self, obj):
        """
        Export object by uid. FK references to uid-enabled models are exported as:
          {"__ref__": true, "model": "app.Model", "uid": "..."}
        Non-domain FKs fall back to the raw *_id column value.
        GeoDjango geometries are exported as:
          {"__geo__": true, "ewkt": "SRID=4326;POINT(...)"}
        """
        data = {"uid": str(obj.uid), "fields": {}}
        for field in obj._meta.fields:
            if field.primary_key or field.name == "uid":
                continue
            if field.is_relation and (field.many_to_one or field.one_to_one):
                related = getattr(obj, field.name)
                if related is None:
                    data["fields"][field.name] = None
                elif hasattr(related, "uid"):
                    data["fields"][field.name] = {
                        "__ref__": True,
                        "model": related._meta.label,
                        "uid": str(related.uid),
                    }
                else:
                    data["fields"][field.attname] = getattr(obj, field.attname)
                continue
            data["fields"][field.name] = self._to_serializable(getattr(obj, field.name))
        return data

    def _to_serializable(self, value):
        """Convert non-JSON-native values to a serializable form."""
        if value is None:
            return None
        # GeoDjango geometry → tagged EWKT
        try:
            from django.contrib.gis.geos import GEOSGeometry
            if isinstance(value, GEOSGeometry):
                return {"__geo__": True, "ewkt": value.ewkt}
        except ImportError:
            pass
        # FileField / ImageField → stored path string
        from django.db.models.fields.files import FieldFile
        if isinstance(value, FieldFile):
            return value.name or ""
        return value

    # ---------------------------------------------------------
    # Hashing
    # ---------------------------------------------------------

    def sha256_file(self, path):
        h = hashlib.sha256()
        with Path(path).open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def verify_hashes(self, tmpdir, manifest):
        tmpdir = Path(tmpdir)
        for rel_path, expected in manifest.get("hashes", {}).items():
            fp = tmpdir / rel_path
            if not fp.exists():
                raise ImproperlyConfigured(f"Backup file missing: {rel_path}")
            if self.sha256_file(fp) != expected:
                raise ImproperlyConfigured(f"Hash mismatch: {rel_path}")

    # ---------------------------------------------------------
    # Signing / verification
    # ---------------------------------------------------------

    def _get_backup_profile(self):
        return getattr(self.platform, "backup_profile", None)

    def _get_private_key(self):
        from toto.gervazy.crypto import GervazyCryptoSession

        profile = self._get_backup_profile()
        epk = profile.signing_key if profile else None
        if not epk:
            raise ImproperlyConfigured("BackupProfile.signing_key is not configured.")
        if not self.vault_password:
            raise ImproperlyConfigured(
                "No vault password available. Set BACKUP_VAULT_PASSWORD in environment."
            )
        pem = GervazyCryptoSession(epk.strongbox, self.vault_password).decrypt_private_key(epk)
        return serialization.load_pem_private_key(pem.encode(), password=None)

    def _get_public_key(self):
        profile = self._get_backup_profile()
        pem = profile.verify_key if profile else None
        if not pem:
            raise ImproperlyConfigured("BackupProfile.verify_key is not configured.")
        return serialization.load_pem_public_key(pem.encode())

    def sign_bytes(self, data: bytes) -> bytes:
        return self._get_private_key().sign(
            data,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )

    def write_signature(self, tmpdir, data: bytes):
        profile = self._get_backup_profile()
        if not profile or not profile.signing_key or not self.vault_password:
            return
        try:
            sig = self.sign_bytes(data)
            (Path(tmpdir) / "signature.txt").write_text(
                base64.b64encode(sig).decode(), encoding="utf-8"
            )
        except Exception:
            pass

    def verify_signature(self, tmpdir):
        tmpdir = Path(tmpdir)
        sig_path = tmpdir / "signature.txt"
        if not sig_path.exists():
            return
        signature = base64.b64decode(sig_path.read_text(encoding="utf-8"))
        try:
            self._get_public_key().verify(
                signature,
                (tmpdir / "manifest.json").read_bytes(),
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
                hashes.SHA256(),
            )
        except Exception:
            raise ImproperlyConfigured("Invalid backup signature.")

    # ---------------------------------------------------------
    # ZIP helpers
    # ---------------------------------------------------------

    def write_zip(self, source_dir, output_path):
        source_dir = Path(source_dir)
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as z:
            for p in source_dir.rglob("*"):
                if p.is_file():
                    z.write(p, p.relative_to(source_dir))

    def extract_zip(self, backup_path):
        """Returns a context manager that extracts the ZIP into a temp dir."""

        class _Extracted:
            def __init__(self, path):
                self._path = Path(path)
                self._tmp = None

            def __enter__(self):
                self._tmp = tempfile.TemporaryDirectory()
                tmpdir = Path(self._tmp.name)
                with zipfile.ZipFile(self._path, "r") as z:
                    z.extractall(tmpdir)
                return tmpdir

            def __exit__(self, *_):
                self._tmp.cleanup()

        return _Extracted(backup_path)

    # ---------------------------------------------------------
    # JSON helpers
    # ---------------------------------------------------------

    def write_json(self, path, payload):
        Path(path).write_text(
            json.dumps(payload, cls=DjangoJSONEncoder, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def read_json(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))
