import hashlib
import os
from toto.vault.strategy.pdf import PdfStrategy
from toto.vault.strategy.image import ImageStrategy
from toto.vault.strategy.text import TextStrategy
from django.urls import reverse
from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify


class Bucket(models.Model):
    name = models.CharField(max_length=100, unique=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    slug = models.SlugField(max_length=120, unique=True)
    storage_quota_mb = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Storage quota per user in MB. Leave blank for unlimited.",
    )

    class Meta:
        verbose_name = "Bucket"
        verbose_name_plural = "Buckets"
        unique_together = ('name', 'owner')

    def __str__(self):
        return f"Bucket {self.name}"


class VaultFile(models.Model):
    FILE_TYPES = [
        ('pdf', 'PDF'),
        ('image', 'Image'),
        ('html', 'HTML'),
        ('text', 'Text File'),
        ('json', 'JSON'),
        ('svg', 'SVG File'),
        ('audio', 'Audio'),
        ('video', 'Video'),
    ]

    @classmethod
    def detect_type(cls, mime: str) -> str:
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
        if "json" in mime:
            return "json"
        if mime.startswith("audio/"):
            return "audio"
        if mime.startswith("video/"):
            return "video"
        return "text"

    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    key = models.SlugField(max_length=255, blank=True)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    file = models.FileField(upload_to='vault/files/')
    file_type = models.CharField(max_length=10, choices=FILE_TYPES)
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
        # Generate key from filename if missing
        if self.file and not self.key:
            base_name = os.path.splitext(os.path.basename(self.file.name))[0]
            candidate_key = slugify(base_name)
            if VaultFile.objects.filter(bucket=self.bucket, key=candidate_key).exists():
                raise ValueError(f"A file with key '{candidate_key}' already exists in this bucket.")
            self.key = candidate_key

        # Capture file size on first save (when file is being attached)
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
        # If key is missing or empty, no public URL can be generated
        if not self.key:
            return None

        # If bucket is missing (shouldn't happen, but safe)
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

    # Each directory may have at most one gateway.
    directory = models.OneToOneField(
        'VaultDirectory',
        on_delete=models.CASCADE,
        related_name='gateway',
    )

    # Denormalised for easy filtering — must always equal directory.bucket.
    bucket = models.ForeignKey(
        Bucket,
        on_delete=models.CASCADE,
        related_name='gateways',
    )

    # Who can use this gateway
    allowed_users = models.ManyToManyField(User, blank=True)

    # Optional description for UI
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
        # Keep bucket in sync with directory so queries on bucket stay valid.
        if self.directory_id:
            self.bucket_id = (
                VaultDirectory.objects
                .filter(pk=self.directory_id)
                .values_list('bucket_id', flat=True)
                .first()
            )
        super().save(*args, **kwargs)


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
        """Return list of VaultDirectory from root down to self."""
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
