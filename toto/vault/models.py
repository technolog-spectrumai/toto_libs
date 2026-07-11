import hashlib
import os
from django.conf import settings
from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify

from toto.vault.strategy.pdf import PdfStrategy
from toto.vault.strategy.image import ImageStrategy
from toto.vault.strategy.text import TextStrategy
from django.urls import reverse


class StorageBackend(models.TextChoices):
    LOCAL = "local", "Local"
    S3 = "s3", "S3-compatible"
    REMOTE_TOTO = "remote_toto", "Remote Toto Server"


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

    def get_connection_url(self) -> str:
        from toto.vault.connection import BucketConnectionSpec
        return BucketConnectionSpec.from_bucket(self).to_url()

    def get_public_file_url(self, file_key: str) -> str:
        if not self.public_base_url:
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
        ('zip', 'Archive'),
    ]
    # Retired doc types (presentation/.pml, notebook/.tpy, contract/.contract) are
    # ordinary 'xml' now, content-sniffed by memo/mandragora/notarius. Existing rows
    # keep their old file_type string (choices aren't DB-enforced) and still open.

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
    file = models.FileField(upload_to='vault/files/')
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

