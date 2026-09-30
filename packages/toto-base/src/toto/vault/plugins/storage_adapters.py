"""The vault's own bucket kinds (``toto.vault.storage_adapters``).

Discovered by ``autodiscover_plugins("plugins.storage_adapters")`` in
``VaultConfig.ready``. Views never branch on these: they ask the adapter.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from toto.vault.storage_adapters import (
    HEALTH_ERROR,
    HEALTH_OK,
    HEALTH_UNKNOWN,
    StorageAdapter,
    _text,
    clean_s3_bucket_name,
    clean_s3_keys,
    clean_s3_prefix,
    field,
    file_totals,
    outbound_url,
)


# ---------------------------------------------------------------------------
# This server
# ---------------------------------------------------------------------------

@StorageAdapter.plugin(key="local", title=gettext_lazy("This Zenobia"), order=10)
class LocalAdapter(StorageAdapter):
    backend = "local"
    icon = "fa-solid fa-server"
    summary = gettext_lazy("Files are stored on this server's own disk.")

    def matches(self, bucket) -> bool:
        return (bucket.storage_backend or "local") in ("", "local")

    def probe(self, bucket):
        from toto.vault.storage import private_storage

        try:
            root = private_storage().location
        except Exception as exc:  # noqa: BLE001 - one sentence, whatever broke
            return self._stamp_probe(bucket, False, str(exc))
        import os

        try:
            os.makedirs(root, exist_ok=True)   # what the storage does on first save
        except OSError:
            pass
        if not os.path.isdir(root) or not os.access(root, os.W_OK):
            return self._stamp_probe(bucket, False, _("This server's storage folder is not writable."))
        return self._stamp_probe(bucket, True, _("Stored on this server."))


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------

class _S3Adapter(StorageAdapter):
    """Common ground of the S3 kinds: the driver, the sealed keys, the probe."""

    backend = "s3"
    icon = "fa-brands fa-aws"
    is_remote = True
    seals_secret = True
    #: The StorageProvider preset this kind uses (seeded by
    #: ``ingress_storage_providers``; recreated from the same definition when
    #: a host never ran the seed).
    provider_name = ""

    def matches(self, bucket) -> bool:
        if bucket.storage_backend != "s3":
            return False
        provider = bucket.provider if bucket.provider_id else None
        return bool(provider and provider.name == self.provider_name)

    def provider(self):
        from toto.vault.management.commands.ingress_storage_providers import _CORE_PROVIDERS
        from toto.vault.models import StorageProvider

        found = StorageProvider.objects.filter(name=self.provider_name).first()
        if found is not None:
            return found
        for name, display, template, region, style, ssl in _CORE_PROVIDERS:
            if name == self.provider_name:
                return StorageProvider.objects.create(
                    name=name, display_name=display, endpoint_url_template=template,
                    default_region=region, addressing_style=style, use_ssl=ssl,
                    is_builtin=True)
        raise ValidationError(_("This server has no preset for this storage provider."))

    # -- inputs ---------------------------------------------------------------

    def regions(self) -> list[tuple[str, str]]:
        return []

    def fields(self):
        return [
            field("bucket_name", _("Bucket at the provider"), max_length=63,
                  help=_("The bucket's own name on the provider's side. It must exist already.")),
            field("region", _("Region"), kind="select", choices=self.regions()),
            field("prefix", _("Folder prefix"), required=False, max_length=200,
                  placeholder="vault/",
                  help=_("Files go under this path in the bucket. Left blank: vault/.")),
            field("access_key_id", _("Access key id"), max_length=128),
            field("secret_access_key", _("Secret key"), kind="password", secret=True,
                  help=_("Stored encrypted and never shown again.")),
        ]

    def clean_region(self, raw) -> str:
        value = str(raw or "").strip()
        if value not in {code for code, _label in self.regions()}:
            raise ValidationError({"region": _("Choose a region from the list.")})
        return value

    def validate(self, data):
        errors = {}
        config = {}
        for name, clean in (("bucket_name", clean_s3_bucket_name), ("region", self.clean_region),
                            ("prefix", clean_s3_prefix)):
            try:
                config[name] = clean(_text(data, name))
            except ValidationError as exc:
                errors.update(exc.message_dict)
        try:
            secret = clean_s3_keys(data)
        except ValidationError as exc:
            errors.update(exc.message_dict)
            secret = {}
        if errors:
            raise ValidationError(errors)
        stored = {"bucket_name": config["bucket_name"], "region_name": config["region"],
                  "prefix": config["prefix"]}
        endpoint = self.endpoint_for(config["region"])
        if endpoint:
            # The SSRF guard, surfaced as a sentence at the door (the driver
            # applies it again on every call).
            outbound_url(endpoint, field_name="region", label=_("Endpoint"))
        return stored, secret

    def endpoint_for(self, region: str) -> str:
        return self.provider().resolve_endpoint_url(region=region)

    # -- the driver -------------------------------------------------------------

    def _candidate(self, config):
        from toto.vault.models import Bucket

        return Bucket(storage_backend="s3", provider=self.provider(), storage_config=dict(config))

    def probe_candidate(self, config, secret):
        from toto.vault.storage_backends import get_bucket_storage

        try:
            return get_bucket_storage(self._candidate(config), credential=dict(secret)).probe()
        except Exception as exc:  # noqa: BLE001 - the outbound guard, a missing boto3
            return False, str(exc)

    def before_create(self, config, secret):
        ok, message = self.probe_candidate(config, secret)
        if not ok:
            raise ValidationError(_("The bucket did not answer the connection test, so it "
                                    "was not saved: %(reason)s") % {"reason": message})

    def build(self, bucket, config, secret):
        bucket.provider = self.provider()
        bucket.storage_config = {k: config[k] for k in ("bucket_name", "region_name", "prefix")
                                 if config.get(k)}

    def store_secret(self, bucket, config, secret):
        from toto.vault.models import BucketSecret

        row = BucketSecret(bucket=bucket)
        row.seal(secret)
        row.save()

    def after_create(self, bucket, config, secret):
        # The probe create just ran counts as the first test.
        self._stamp_probe(bucket, True, _("The bucket answered."))

    def probe(self, bucket):
        from toto.vault.storage_backends import get_bucket_storage

        try:
            ok, message = get_bucket_storage(bucket).probe()
        except Exception as exc:  # noqa: BLE001 - guard refusals, unreadable secret
            ok, message = False, str(exc)
        return self._stamp_probe(bucket, ok, message)

    # -- describing -------------------------------------------------------------

    def target(self, bucket) -> str:
        config = bucket.storage_config or {}
        parts = [f"{config.get('bucket_name', '?')}/{config.get('prefix', 'vault/') or ''}"]
        if config.get("region_name"):
            parts.append(config["region_name"])
        if bucket.provider_id:
            parts.append(bucket.provider.display_name)
        elif config.get("endpoint_url"):
            # A bucket made before Management, with a typed endpoint: where
            # its bytes go is that address (the superuser's list only —
            # Bucket.remote_label never names it).
            parts.append(config["endpoint_url"])
        return " · ".join(parts)

    def credential_label(self, bucket) -> str:
        from toto.vault.models import BucketSecret

        hint = BucketSecret.objects.filter(bucket_id=bucket.pk).values_list("hint", flat=True).first()
        if hint is not None:
            return _("Stored key …%(hint)s") % {"hint": hint}
        return _("Server environment")

    def destroy_plan(self, bucket):
        files, size = file_totals(bucket)
        config = bucket.storage_config or {}
        return {
            "files": files, "bytes": size,
            "removes": [
                _("Every file in the bucket (%(count)s), and its object in %(target)s.")
                % {"count": files, "target": config.get("bucket_name", "?")},
                _("Its folders, upload gateways, clearance keeping and shares with other Zenobias."),
                _("The stored access key."),
            ],
            "keeps": [
                _("The bucket at the provider itself, and every object this vault did not store in it."),
            ],
        }


@StorageAdapter.plugin(key="aws_s3", title=gettext_lazy("Amazon S3"), order=20)
class AwsS3Adapter(_S3Adapter):
    provider_name = "aws"
    icon = "fa-brands fa-aws"
    summary = gettext_lazy("Files are stored in an Amazon S3 bucket you already have.")

    #: The commercial regions; a host adds others with VAULT_AWS_REGIONS.
    REGIONS = (
        "us-east-1", "us-east-2", "us-west-1", "us-west-2", "ca-central-1",
        "eu-central-1", "eu-central-2", "eu-west-1", "eu-west-2", "eu-west-3",
        "eu-north-1", "eu-south-1", "eu-south-2", "ap-northeast-1", "ap-northeast-2",
        "ap-northeast-3", "ap-southeast-1", "ap-southeast-2", "ap-south-1",
        "sa-east-1", "me-central-1", "il-central-1", "af-south-1",
    )

    def regions(self):
        codes = tuple(getattr(settings, "VAULT_AWS_REGIONS", ()) or self.REGIONS)
        return [(code, code) for code in codes]


@StorageAdapter.plugin(key="ovh_s3", title=gettext_lazy("OVH Object Storage"), order=30)
class OvhS3Adapter(_S3Adapter):
    provider_name = "ovh"
    icon = "fa-solid fa-cloud"
    summary = gettext_lazy("Files are stored in an OVHcloud Object Storage (S3) bucket you already have.")

    #: OVH's S3 regions; the region picks the endpoint
    #: (``https://s3.<region>.io.cloud.ovh.net``). A host adds others with
    #: VAULT_OVH_REGIONS. A fixed list, because the region is spelled into a
    #: URL this server then calls.
    REGIONS = (
        ("gra", "Gravelines (gra)"), ("sbg", "Strasbourg (sbg)"), ("rbx", "Roubaix (rbx)"),
        ("de", "Frankfurt (de)"), ("uk", "London (uk)"), ("waw", "Warsaw (waw)"),
        ("bhs", "Beauharnois (bhs)"), ("ca-east-tor", "Toronto (ca-east-tor)"),
        ("eu-west-par", "Paris (eu-west-par)"), ("eu-south-mil", "Milan (eu-south-mil)"),
        ("ap-southeast-sgp", "Singapore (ap-southeast-sgp)"),
        ("ap-southeast-syd", "Sydney (ap-southeast-syd)"),
        ("ap-south-mum", "Mumbai (ap-south-mum)"),
        ("us-east-va", "Vint Hill (us-east-va)"), ("us-west-or", "Hillsboro (us-west-or)"),
    )

    def regions(self):
        extra = getattr(settings, "VAULT_OVH_REGIONS", ()) or ()
        return list(self.REGIONS) + [(code, code) for code in extra]


@StorageAdapter.plugin(key="s3", title=gettext_lazy("S3-compatible"), order=90)
class OtherS3Adapter(_S3Adapter):
    """S3 buckets made before Management (the admin, the old Remote tab):
    described, tested and deleted like the others, never offered by Create —
    their endpoint was typed, not picked from a preset."""

    creatable = False
    icon = "fa-solid fa-cloud"

    def matches(self, bucket) -> bool:
        return bucket.storage_backend == "s3"


# ---------------------------------------------------------------------------
# Another Zenobia
# ---------------------------------------------------------------------------

RIGHT_LABELS = {
    "may_list": gettext_lazy("list"),
    "may_download": gettext_lazy("download"),
    "may_upload": gettext_lazy("upload"),
    "may_delete": gettext_lazy("delete"),
}


@StorageAdapter.plugin(key="zenobia_remote", title=gettext_lazy("Another Zenobia"), order=40)
class ZenobiaRemoteAdapter(StorageAdapter):
    """A bucket another Zenobia shares, connected with the pairing code its
    operator minted there (``peering.pairing_code_for``).

    The code carries the grant's id, its magic token and its api key: all
    three go into the ``secret`` half and end up on a ``BucketPeer`` (the key
    Fernet-sealed, the token as the URL segment it is) — never in the config,
    a draft, a page or the audit chain.
    """

    backend = "remote_toto"
    icon = "fa-solid fa-link"
    is_remote = True
    seals_secret = True
    summary = gettext_lazy("Connect a bucket another Zenobia shares with you, using its pairing code.")

    def fields(self):
        from toto.vault.peering import federated_host_choices

        return [
            field("pairing_code", _("Pairing code"), kind="textarea", secret=True,
                  help=_("Created once on the other Zenobia, under Share with another Zenobia.")),
            field("base_url", _("Address of the other Zenobia"), required=False,
                  choices=federated_host_choices(), placeholder="https://zenobia.example.org",
                  help=_("Left blank, the address the code names is used.")),
        ]

    # -- the code -------------------------------------------------------------

    @staticmethod
    def _decode(code) -> dict:
        from toto.vault.peering import decode_pairing_code

        if not str(code or "").strip():
            raise ValidationError({"pairing_code": _("Paste the pairing code.")})
        try:
            return decode_pairing_code(str(code))
        except ValidationError as exc:
            raise ValidationError({"pairing_code": exc.messages})

    @staticmethod
    def _expiry(payload):
        from django.utils.dateparse import parse_datetime

        from django.utils import timezone

        raw = payload.get("expires_at")
        if not raw:
            return None
        try:
            value = parse_datetime(str(raw))
        except (TypeError, ValueError):
            return None
        if value is not None and timezone.is_naive(value):
            import datetime

            value = timezone.make_aware(value, datetime.timezone.utc)
        return value

    @staticmethod
    def connected_as(payload) -> str:
        """The label of the mount this code already made here, or ""."""
        from toto.vault.peering import BucketPeer

        peer = BucketPeer.objects.filter(grant_uid=str(payload.get("grant_uid") or "")).first() \
            if _uuid_ok(payload.get("grant_uid")) else None
        return peer.label if peer is not None else ""

    def decode_preview(self, code) -> dict:
        """What a code grants — host, remote bucket, rights, expiry — WITHOUT
        saving anything and without anything secret in the answer."""
        from django.utils import timezone

        payload = self._decode(code)
        expires_at = self._expiry(payload)
        rights = [r for r in payload.get("rights") or [] if r in RIGHT_LABELS]
        return {
            "host": str(payload.get("host") or ""),
            "bucket": str(payload.get("bucket") or ""),
            "rights": rights,
            "rights_labels": [str(RIGHT_LABELS[r]) for r in rights],
            "expires_at": expires_at,
            "expired": bool(expires_at and expires_at <= timezone.now()),
            "already_connected": self.connected_as(payload),
            "lacks_list": _lacks_list(payload),
        }

    def validate(self, data):
        from django.utils import timezone

        payload = self._decode(data.get("pairing_code", "") if hasattr(data, "get") else "")
        if not _uuid_ok(payload.get("grant_uid")):
            raise ValidationError({"pairing_code": _(
                "That does not decode as a pairing code. Paste the whole code, "
                "with no surrounding quotes or line breaks.")})
        expires_at = self._expiry(payload)
        if expires_at and expires_at <= timezone.now():
            raise ValidationError({"pairing_code": _(
                "This pairing code has expired. Ask the other Zenobia's operator for a new one.")})
        label = self.connected_as(payload)
        if label:
            raise ValidationError({"pairing_code": _(
                "This pairing code is already connected here, as '%(label)s'.") % {"label": label}})
        if _lacks_list(payload):
            raise ValidationError({"pairing_code": lacks_list_sentence()})
        raw_url = _text(data, "base_url") or str(payload.get("host") or "")
        if not raw_url:
            raise ValidationError({"base_url": _("Enter the other Zenobia's address.")})
        base_url = outbound_url(raw_url, field_name="base_url",
                                label=_("Address of the other Zenobia")).rstrip("/")
        rights = [r for r in payload.get("rights") or [] if r in RIGHT_LABELS]
        config = {
            "base_url": base_url,
            "remote_bucket": str(payload.get("bucket") or ""),
            "rights": rights,
            "expires_at": expires_at.isoformat() if expires_at else "",
        }
        secret = {
            "grant_uid": str(payload["grant_uid"]),
            "magic_token": str(payload["magic_token"]),
            "api_key": str(payload["api_key"]),
        }
        return config, secret

    # -- the peer -------------------------------------------------------------

    @staticmethod
    def _peer(label, config, secret):
        from toto.vault.peering import BucketPeer

        return BucketPeer(
            label=label, base_url=config["base_url"], grant_uid=secret["grant_uid"],
            magic_token=secret["magic_token"], remote_bucket_slug=config.get("remote_bucket", ""),
            capabilities=list(config.get("rights") or []))

    def probe_candidate(self, config, secret):
        from toto.vault.peer_client import PeerClient

        peer = self._peer(_("the other Zenobia"), config, secret)
        try:
            manifest = PeerClient(peer, api_key=secret["api_key"]).manifest()
        except Exception as exc:  # noqa: BLE001 - one sentence, whatever failed
            return False, _scrub(str(exc), secret)
        return True, _("The other Zenobia answered for bucket '%(bucket)s'.") % {
            "bucket": manifest.get("bucket") or config.get("remote_bucket", "")}

    def before_create(self, config, secret):
        """Asked again at Create: the guided flow validates a code, shows it,
        tests it — and minutes may pass before Connect."""
        from django.utils import timezone
        from django.utils.dateparse import parse_datetime

        label = self.connected_as({"grant_uid": secret.get("grant_uid")})
        if label:
            raise ValidationError({"pairing_code": _(
                "This pairing code is already connected here, as '%(label)s'.") % {"label": label}})
        expires_at = parse_datetime(config.get("expires_at") or "") if config.get("expires_at") else None
        if expires_at and expires_at <= timezone.now():
            raise ValidationError({"pairing_code": _(
                "This pairing code has expired. Ask the other Zenobia's operator for a new one.")})
        for key in ("grant_uid", "magic_token", "api_key"):
            if not secret.get(key):
                raise ValidationError({"pairing_code": _("Paste the pairing code.")})

    def build(self, bucket, config, secret):
        peer = self._peer(bucket.name, config, secret)
        peer.paired_by = bucket.created_by
        peer.set_api_key(secret["api_key"])
        peer.save()
        bucket.peer = peer

    def after_create(self, bucket, config, secret):
        # The first probe, after the commit: a failure is stamped on the
        # pairing, it does not undo it (the admin's and the old page's rule).
        self.probe(bucket)

    def probe(self, bucket):
        from toto.vault.peer_client import PeerClient
        from toto.vault.peering import BucketPeer, apply_manifest

        peer = bucket.peer if bucket.peer_id else None
        if peer is None:
            return False, _("This bucket has no connection to another Zenobia.")
        try:
            manifest = PeerClient(peer).manifest()
        except Exception as exc:  # noqa: BLE001 - one sentence, stamped
            message = _scrub(str(exc), {"magic_token": peer.magic_token,
                                        "grant_uid": str(peer.grant_uid)})
            BucketPeer.objects.filter(pk=peer.pk).update(probe_error=message, last_error=message)
            peer.probe_error = peer.last_error = message
            return False, message
        apply_manifest(peer, manifest)
        return True, _("The other Zenobia answered for bucket '%(bucket)s'.") % {
            "bucket": peer.remote_bucket_slug}

    # -- describing -------------------------------------------------------------

    def target(self, bucket) -> str:
        peer = bucket.peer if bucket.peer_id else None
        if peer is None:
            return _("No connection")
        return f"{peer.base_url} · {peer.remote_bucket_slug or '?'}"

    def health(self, bucket) -> str:
        peer = bucket.peer if bucket.peer_id else None
        if peer is None or peer.last_error:
            return HEALTH_ERROR
        if peer.last_ok_at:
            return HEALTH_OK
        return HEALTH_UNKNOWN

    def health_detail(self, bucket) -> str:
        peer = bucket.peer if bucket.peer_id else None
        if peer is None:
            return _("This bucket has no connection to another Zenobia.")
        return peer.last_error or ""

    def credential_label(self, bucket) -> str:
        peer = bucket.peer if bucket.peer_id else None
        if peer is None:
            return ""
        rights = [str(RIGHT_LABELS[r]) for r in (peer.capabilities or []) if r in RIGHT_LABELS]
        return ", ".join(rights) or _("no rights")

    def destroy_plan(self, bucket):
        files, size = file_totals(bucket)
        return {
            "files": files, "bytes": size,
            "removes": [
                _("The connection to the other Zenobia, and the %(count)s file listings shown here.")
                % {"count": files},
                _("Its folders, clearance keeping and the stored pairing, when no other bucket uses it."),
            ],
            "keeps": [
                _("Every file on the other Zenobia — nothing is deleted there."),
            ],
        }


def _lacks_list(payload) -> bool:
    """The code states its rights and List is not among them. Connecting reads
    the share's manifest, which the other Zenobia answers only with List
    (``peer_views.peer_manifest``) — such a code can never be connected. A
    code that states no rights at all is not judged here."""
    rights = payload.get("rights") if isinstance(payload, dict) else None
    return isinstance(rights, list) and "may_list" not in rights


def lacks_list_sentence() -> str:
    return _("This share does not include List, and connecting needs it: this Zenobia reads "
             "the list of the bucket's files to connect it. Ask the other Zenobia's operator "
             "for a new share with List ticked.")


def _uuid_ok(value) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return False
    return True


def _scrub(text: str, secret: dict) -> str:
    """A transport error can quote the URL it called, and a peer URL carries
    the grant's magic token: never let one reach a page, a stamp or JSON."""
    for name in ("magic_token", "api_key", "grant_uid"):
        value = str((secret or {}).get(name) or "")
        if len(value) >= 6:
            text = text.replace(value, "…")
    return text
