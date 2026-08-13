import hashlib
import os
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent
from toto.vault.strategy.pdf import PdfStrategy
from toto.vault.strategy.image import ImageStrategy
from toto.vault.strategy.text import TextStrategy
from django.urls import reverse


from .storage import private_storage


class StorageBackend(models.TextChoices):
    LOCAL = "local", "Local"
    S3 = "s3", "S3-compatible"
    REMOTE_TOTO = "remote_toto", "Remote Toto Server"


def external_buckets_allowed() -> bool:
    """Host contract flag (LOCATIONS_GEOCODING / USE_EXTERNAL_FONTS register):
    a host sets ``VAULT_EXTERNAL_BUCKETS = False`` (faros does) to forbid any
    bucket whose storage is not this server's own filesystem — no S3, no
    remote-toto proxying, no public CDN base URLs. Default keeps the full
    backend matrix (zenobia unchanged)."""
    return getattr(settings, "VAULT_EXTERNAL_BUCKETS", True)


def file_edits_allowed() -> bool:
    """Host contract flag: a host sets ``VAULT_FILE_EDITS = False`` (faros
    does) to refuse every server-side rewrite of stored file CONTENT — the
    browser editors, the desktop content API, empty-file creation. Upload,
    download, delete, encrypt/decrypt and zip stay available."""
    return getattr(settings, "VAULT_FILE_EDITS", True)


class StorageProvider(models.Model):
    """
    A named S3-compatible provider preset (AWS, OVH, MinIO, …).
    Seeded by ingress_storage_providers; user-extensible via admin.
    """

    name = models.SlugField(max_length=64, unique=True)
    display_name = models.CharField(max_length=128)
    endpoint_url_template = models.CharField(
        max_length=256,
        blank=True,
        help_text=(
            "Endpoint URL, optionally with {region} or {account_id} placeholders. "
            "Leave blank for AWS default routing."
        ),
    )
    default_region = models.CharField(max_length=64, blank=True)
    addressing_style = models.CharField(
        max_length=8,
        choices=[("path", "Path"), ("virtual", "Virtual"), ("auto", "Auto")],
        default="auto",
    )
    use_ssl = models.BooleanField(default=True)
    is_builtin = models.BooleanField(
        default=True,
        help_text="Seeded by ingress — safe to re-run ingress to reset.",
    )

    class Meta:
        verbose_name = "Storage Provider"
        verbose_name_plural = "Storage Providers"
        ordering = ["display_name"]

    def __str__(self):
        return self.display_name

    def resolve_endpoint_url(self, region: str = "", account_id: str = "", **kwargs) -> str:
        """Interpolate placeholders in the endpoint template. Returns empty string for AWS."""
        if not self.endpoint_url_template:
            return ""
        try:
            return self.endpoint_url_template.format(
                region=region or self.default_region,
                account_id=account_id,
                **kwargs,
            )
        except KeyError:
            return self.endpoint_url_template


class Bucket(models.Model):
    name = models.CharField(max_length=100, unique=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    slug = models.SlugField(max_length=120, unique=True)
    storage_quota_mb = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Storage quota per user in MB. Leave blank for unlimited.",
    )
    storage_backend = models.CharField(
        max_length=16,
        choices=StorageBackend.choices,
        default=StorageBackend.LOCAL,
        help_text="Storage backend for files in this bucket.",
    )
    storage_config = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Non-secret backend config. "
            "S3: bucket_name, region_name, prefix, use_ssl, addressing_style, aws_profile. "
            "remote_toto: server_url, bucket_slug, api_token_env. "
            "Credentials must come from environment variables, not this field."
        ),
    )
    provider = models.ForeignKey(
        StorageProvider,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="buckets",
        help_text="Provider preset used when storage_backend is S3-compatible.",
    )
    public_base_url = models.URLField(
        blank=True,
        default="",
        help_text=(
            "Optional CDN or public base URL (e.g. https://cdn.example.com/vault/). "
            "When set, get_public_file_url() returns a direct link per file."
        ),
    )

    class Meta:
        verbose_name = "Bucket"
        verbose_name_plural = "Buckets"
        unique_together = ('name', 'owner')

    def __str__(self):
        return f"Bucket {self.name}"

    def clean(self):
        super().clean()
        # Model-level belt for the host contract: whatever code path tries to
        # persist a non-local bucket on a local-only host must fail loudly.
        if self.storage_backend != StorageBackend.LOCAL and not external_buckets_allowed():
            raise ValidationError("External buckets are disabled on this host.")

    def get_connection_url(self) -> str:
        from toto.vault.connection import BucketConnectionSpec
        return BucketConnectionSpec.from_bucket(self).to_url()

    def get_public_file_url(self, file_key: str) -> str:
        # A public base URL is a third-party origin — off with external buckets.
        if not self.public_base_url or not external_buckets_allowed():
            return ""
        base = self.public_base_url.rstrip("/")
        return f"{base}/{file_key}"


class VaultFile(models.Model):
    FILE_TYPES = [
        ('pdf', 'PDF'),
        ('image', 'Image'),
        ('html', 'HTML'),
        ('text', 'Text File'),
        ('json', 'JSON'),
        ('yaml', 'YAML'),
        ('xml', 'XML'),
        ('latex', 'LaTeX'),
        ('bib', 'Bibliography'),
        ('csv', 'CSV'),
        ('svg', 'SVG File'),
        ('audio', 'Audio'),
        ('video', 'Video'),
        ('python', 'Python'),
        ('neojson', 'NeoJSON'),
        ('sheet', 'Primula Sheet'),   # a Univer workbook snapshot (JSON), edited in toto.primula
        ('presentation', 'Presentation'),  # a slide deck (XML), edited in toto.memo
        ('document', 'Document'),     # a written document (XML), edited in toto.cyprian
        ('zip', 'Archive'),
    ]
    # The retired doc types (notebook/.tpy, contract/.contract) are ordinary 'xml'
    # now, content-sniffed by mandragora/notarius. Existing rows keep their old
    # file_type string (choices aren't DB-enforced) and still open.
    #
    # 'presentation' came BACK, and the reason is worth knowing before removing it
    # again. The plugin registries are dict[key -> plugin] and `for_file_type` is
    # `registry.get(file_type)`, so a plugin only ever fires when its `key` equals
    # a file_type — and `key="xml"` is already taken by toto.editor, with
    # BasePlugin.register raising on a duplicate. With decks typed 'xml' there was
    # therefore no way to give them a Play button at all, and Edit opened the
    # generic XML editor. What is retired is the '.pml' EXTENSION, not the type:
    # deck files are still named .xml and _EXT_MAP still has no .pml entry.

    _EXT_MAP = {
        ".tex": "latex", ".sty": "latex", ".cls": "latex", ".dtx": "latex", ".ins": "latex",
        ".bib": "bib",
        ".pdf": "pdf",
        ".svg": "svg",
        ".csv": "csv",
        ".json": "json",
        ".neojson": "neojson",
        ".yaml": "yaml", ".yml": "yaml",
        ".xml": "xml",
        ".html": "html", ".htm": "html",
        ".md": "text", ".txt": "text", ".rst": "text",
        ".py": "python",
        ".mp3": "audio", ".ogg": "audio", ".wav": "audio", ".flac": "audio", ".aac": "audio",
        ".mp4": "video", ".mov": "video", ".avi": "video", ".mkv": "video", ".webm": "video",
        ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
        ".webp": "image", ".bmp": "image", ".tiff": "image",
        ".zip": "zip",
    }

    @classmethod
    def detect_type(cls, mime: str, filename: str = "") -> str:
        # Extension-first for types browsers mis-label as text/plain
        if filename:
            ext = os.path.splitext(filename)[1].lower()
            if ext in cls._EXT_MAP:
                return cls._EXT_MAP[ext]
        if not mime:
            return "text"
        mime = mime.lower()
        if "pdf" in mime:
            return "pdf"
        if mime == "image/svg+xml":
            return "svg"
        if mime.startswith("image/"):
            return "image"
        if "html" in mime:
            return "html"
        if "neojson" in mime:
            return "neojson"
        if "json" in mime:
            return "json"
        if "yaml" in mime:
            return "yaml"
        if "xml" in mime:
            return "xml"
        if "latex" in mime or mime in ("application/x-tex", "application/x-latex", "text/x-tex"):
            return "latex"
        if "bibtex" in mime:
            return "bib"
        if "csv" in mime:
            return "csv"
        if mime.startswith("audio/"):
            return "audio"
        if mime.startswith("video/"):
            return "video"
        if "zip" in mime:  # application/zip, application/x-zip-compressed
            return "zip"
        return "text"

    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    key = models.SlugField(max_length=255, blank=True)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    # Private storage, not the default one: MEDIA_ROOT is web-served and this is
    # not web-servable. Same location, so no file moves; no base_url, so nothing
    # can mint a link to it. See toto/vault/storage.py.
    file = models.FileField(upload_to='vault/files/', storage=private_storage)
    file_type = models.CharField(max_length=16, choices=FILE_TYPES)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_encrypted = models.BooleanField(default=False)
    is_public = models.BooleanField(default=False, help_text="If true, file is visible to others")
    notes = models.TextField(blank=True, null=True)
    file_size_bytes = models.PositiveBigIntegerField(
        default=0,
        help_text="File size in bytes, captured at upload time.",
    )
    bucket = models.ForeignKey(Bucket, on_delete=models.SET_NULL, null=True, blank=True, related_name='files')
    directory = models.ForeignKey(
        'VaultDirectory', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='files'
    )

    class Meta:
        verbose_name = "Vault File"
        verbose_name_plural = "Vault Files"
        unique_together = ('bucket', 'key')

    def __str__(self):
        return f"{self.title} ({self.owner.username})"

    def save(self, *args, **kwargs):
        if self.file and not self.key:
            base_name = os.path.splitext(os.path.basename(self.file.name))[0]
            candidate_key = slugify(base_name)
            if VaultFile.objects.filter(bucket=self.bucket, key=candidate_key).exists():
                raise ValueError(f"A file with key '{candidate_key}' already exists in this bucket.")
            self.key = candidate_key

        if self.file and not self.file_size_bytes:
            try:
                self.file_size_bytes = self.file.size
            except Exception:
                pass

        super().save(*args, **kwargs)

    def create_hash(self):
        if self.file and hasattr(self.file, 'read'):
            try:
                self.file.seek(0)
                content = self.file.read()
                self.file.seek(0)
                return hashlib.sha256(content).hexdigest()
            except Exception:
                return None
        return None

    def get_file_info(self):
        return {
            "title": self.title,
            "owner": self.owner.username,
            "type": self.file_type,
            "encrypted": self.is_encrypted,
            "public": self.is_public,
            "uploaded": self.uploaded_at.strftime("%Y-%m-%d"),
            "notes": self.notes or "—"
        }

    def get_strategy(self):
        if self.file_type == 'pdf':
            return PdfStrategy()
        if self.file_type == 'image':
            return ImageStrategy()
        return TextStrategy()

    def encrypt(self, password: str, owner_password=None):
        if self.is_encrypted:
            return
        strategy = self.get_strategy()
        if self.file_type == 'pdf' and not owner_password:
            owner_password = password
        strategy.encrypt(self, password=password, owner_password=owner_password)

    def decrypt(self, password: str):
        if not self.is_encrypted:
            raise ValueError("File is not encrypted.")
        strategy = self.get_strategy()
        strategy.decrypt(self, password=password)

    def get_public_url(self):
        if not self.key:
            return None
        if not self.bucket:
            return None
        try:
            return reverse('vault:public_file', args=[self.bucket.slug, self.key])
        except Exception:
            return None


class FileGateway(models.Model):
    """
    A user-facing upload gateway tied to exactly one directory.
    One gateway per directory; root-level uploads are not allowed via gateway.
    """

    name = models.CharField(max_length=200)

    directory = models.OneToOneField(
        'VaultDirectory',
        on_delete=models.CASCADE,
        related_name='gateway',
    )

    bucket = models.ForeignKey(
        Bucket,
        on_delete=models.CASCADE,
        related_name='gateways',
    )

    allowed_users = models.ManyToManyField(User, blank=True)

    description = models.TextField(blank=True, null=True)

    make_public = models.BooleanField(
        default=False,
        help_text="If enabled, all files uploaded through this gateway become public.",
    )

    max_file_size = models.PositiveIntegerField(
        default=10 * 1024,
        help_text="Maximum allowed file size in KB.",
    )

    class Meta:
        verbose_name = "File Gateway"
        verbose_name_plural = "File Gateways"

    def __str__(self):
        return f"Gateway → {self.directory}"

    def save(self, *args, **kwargs):
        if self.directory_id:
            self.bucket_id = (
                VaultDirectory.objects
                .filter(pk=self.directory_id)
                .values_list('bucket_id', flat=True)
                .first()
            )
        super().save(*args, **kwargs)


class BucketCopyLog(models.Model):
    from_bucket = models.ForeignKey(
        Bucket, on_delete=models.SET_NULL, null=True, related_name="copies_out"
    )
    to_bucket = models.ForeignKey(
        Bucket, on_delete=models.SET_NULL, null=True, related_name="copies_in"
    )
    performed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    file_count = models.PositiveIntegerField(default=1)
    performed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Bucket Copy Log"
        verbose_name_plural = "Bucket Copy Logs"
        ordering = ["-performed_at"]

    def __str__(self):
        return f"{self.from_bucket} → {self.to_bucket} ({self.file_count} files)"


class VaultDirectory(models.Model):
    """
    A named folder inside a Bucket. May be nested (parent → subdirectories).
    Access is restricted to allowed_users when the whitelist is non-empty.
    """

    name = models.CharField(max_length=200)
    bucket = models.ForeignKey(Bucket, on_delete=models.CASCADE, related_name='directories')
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_directories')
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE,
        null=True, blank=True, related_name='subdirectories'
    )
    allowed_users = models.ManyToManyField(
        User, blank=True, related_name='accessible_directories',
        help_text="Leave empty to allow all authenticated users."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Vault Directory"
        verbose_name_plural = "Vault Directories"
        unique_together = ('bucket', 'parent', 'name')

    def __str__(self):
        return self.full_path()

    def full_path(self):
        parts = []
        node = self
        while node is not None:
            parts.append(node.name)
            node = node.parent
        return "/".join(reversed(parts))

    def breadcrumb(self):
        crumbs = []
        node = self
        while node is not None:
            crumbs.append(node)
            node = node.parent
        return list(reversed(crumbs))

    def user_can_access(self, user):
        if not user or not user.is_authenticated:
            return not self.allowed_users.exists()
        if user.is_superuser:
            return True
        if not self.allowed_users.exists():
            return True
        return self.allowed_users.filter(pk=user.pk).exists()



# ---------------------------------------------------------------------------
# Version history
# ---------------------------------------------------------------------------
# Editors (cyprian, memo, primula) autosave straight onto the live VaultFile.
# A VERSION is something else: a deliberate act by a person who decided this
# state was worth keeping and gave it a name. That distinction is the whole
# design — versions number in the handful, not the hundreds, which is what makes
# a plain table sufficient where a delta-compressing engine would otherwise be
# needed.
#
# Lives in vault rather than in the three editors because a version of a vault
# file is a vault concept: vault owns the storage backends, the access rules and
# the bytes. Putting it here also makes every file type versionable rather than
# only those three.


def version_blob_path(instance, filename):
    """``vault/versions/ab/abcdef…`` — the digest IS the address.

    Fanned out by the first two hex characters so no single directory holds
    every blob a busy host ever wrote; the same shape git uses for loose
    objects, and for the same reason.
    """
    digest = instance.content_hash or "unknown"
    return f"vault/versions/{digest[:2]}/{digest}"


class VersionBlob(models.Model):
    """One immutable body, stored once no matter how many versions cite it.

    The repo's first content-addressed store. Everything else that hashes bytes
    here — ``VaultFile.content_hash``, the backup manifest, the ledger chain —
    keeps the digest *beside* a path-addressed object; this keys the object BY
    the digest, which is what makes dedupe possible at all.

    What that buys, concretely: restoring v3 and saving again creates a new
    version pointing at v3's existing blob and writes no bytes. Saving twice
    without changing anything is free. Documents here carry base64 images inline
    by design, so an unchanged 8 MB illustration is exactly the thing that must
    not be stored twice.
    """

    content_hash = models.CharField(
        max_length=64, unique=True,
        help_text="sha256 of the bytes. Unique — this is the dedupe.")
    data = models.FileField(upload_to=version_blob_path)
    size_bytes = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Version blob"
        verbose_name_plural = "Version blobs"

    def __str__(self):
        return f"{self.content_hash[:12]}… ({self.size_bytes} B)"

    def save(self, *args, **kwargs):
        # Immutable by construction: a blob's identity IS its content, so an
        # edited blob would be a different blob. Guarding here rather than
        # trusting callers, the LedgerTransaction idiom.
        if self.pk is not None:
            raise ValidationError(
                "A version blob is addressed by its own digest and cannot be "
                "edited — write a new one.")
        super().save(*args, **kwargs)



class FileLock(models.Model):
    """Who is editing this file right now. One holder, enforced by the database.

    The FIRST of three layers. This one prevents the collision; the editors'
    ``base_hash`` check detects the ones that slip past it (an expired lock, two
    tabs of one person); and a conflicting draft rescues whatever the first two
    missed. Each layer is allowed to fail because the next one catches it —
    the same arrangement the mint chain uses for its single head.

    ``OneToOneField`` is the guarantee, not the service: two people cannot both
    hold a file because the database will not store two rows for it.

    HARD. A non-holder gets the document read-only and their save is refused
    with 423 by the server, not merely hidden by the template. A lock only the
    UI respects is advice.

    SHORT-LIVED. The open editor refreshes ``expires_at`` on a heartbeat; a
    closed laptop therefore frees the document within a couple of minutes and
    nobody ever has to break a lock by hand. Expiry is checked on read rather
    than swept, so a stale row is simply not a lock.

    Not to be confused with the "edit lock" in vault/tests.py, which is about
    encrypted files being barred from editors entirely.
    """

    file = models.OneToOneField(VaultFile, on_delete=models.CASCADE,
                                related_name="edit_lock")
    holder = models.ForeignKey(User, on_delete=models.CASCADE,
                               related_name="held_file_locks")
    acquired_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        verbose_name = "File editing lock"
        verbose_name_plural = "File editing locks"

    def __str__(self):
        return f"{self.file.title} held by {self.holder}"

    @property
    def is_live(self) -> bool:
        from django.utils import timezone

        return self.expires_at > timezone.now()


class FileVersion(models.Model):
    """One saved state of a vault file, and who decided to save it.

    ONE LINEAR HISTORY PER FILE. There is no branching and no merge: the three
    editors' formats are not mergeable (cyprian's whole body is a single CDATA
    line, primula's whole workbook is a single JSON line), and every product
    that ships rich-document editing answers concurrency with either real-time
    collaboration or a conflicting copy — never a merge UI. ``author`` is
    attribution, not a branch.

    APPEND-ONLY. Restoring v3 writes the live file and records v8 "restored
    from v3"; it never rewinds ``number``. History is not rewritten here for the
    same reason it is not rewritten in the ledger or the mint chain.
    """

    file = models.ForeignKey(VaultFile, on_delete=models.CASCADE,
                             related_name="versions")
    #: What the user sees: v1, v2, v3. Per file, monotonic, never reused.
    number = models.PositiveIntegerField()
    blob = models.ForeignKey(VersionBlob, on_delete=models.PROTECT,
                             related_name="versions")
    #: A name the author gave it. A LABELLED version is never auto-pruned — it
    #: was a conscious act, and silently deleting it would break the very idea
    #: the feature rests on.
    label = models.CharField(max_length=200, blank=True)
    author = models.ForeignKey(User, null=True, blank=True,
                               on_delete=models.SET_NULL,
                               related_name="file_versions")
    #: True when this body LOST an optimistic-concurrency check and was kept
    #: rather than thrown away. Exempt from pruning like any labelled version.
    is_conflict = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-number"]
        verbose_name = "File version"
        verbose_name_plural = "File versions"
        constraints = [
            # Two versions numbered v4 on one file would make "restore v4"
            # ambiguous, which is the one thing the UI must never be.
            models.UniqueConstraint(fields=["file", "number"],
                                    name="vault_one_version_per_number"),
        ]
        indexes = [models.Index(fields=["file", "-number"],
                                name="vault_version_file_idx")]

    def __str__(self):
        return f"{self.file.title} v{self.number}"

    @property
    def is_pinned(self) -> bool:
        """Never auto-pruned: somebody named it, or it is rescued work."""
        return bool(self.label) or self.is_conflict

    def read(self) -> bytes:
        with self.blob.data.open("rb") as handle:
            return handle.read()

# ---------------------------------------------------------------------------
# Usage metering
# ---------------------------------------------------------------------------
# Vault owns its own quota tables rather than sharing a central pair — see
# toto.quota.models for why. Metrics: storage.request (one upload) and
# storage.transfer_mb (its size).

class VaultUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Vault usage event"
        verbose_name_plural = "Vault usage events"


class VaultQuotaPolicy(AbstractQuotaPolicy):
    events = VaultUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Vault quota policy"
        verbose_name_plural = "Vault quota policies"
