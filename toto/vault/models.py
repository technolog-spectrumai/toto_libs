import hashlib
import os
from toto.vault.strategy.pdf import PdfStrategy
from toto.vault.strategy.image import ImageStrategy
from toto.vault.strategy.text import TextStrategy
from django.conf import settings
from django.urls import reverse
from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify


class StorageAccount(models.Model):
    """
    Links a vault user to a LedgerAccount for billing.
    Created explicitly by the user via the connect-account flow.
    Used by vault billing to resolve the payer account without magic code lookups.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="storage_account",
    )
    ledger_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="storage_accounts",
    )
    name = models.CharField(max_length=255, blank=True, help_text="Display name for this storage account.")
    authorization = models.ForeignKey(
        "assets.WalletAuthorization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="storage_accounts",
        help_text="If set, vault transactions are auto-authorized. Otherwise the user must confirm manually.",
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_auto_authorized(self) -> bool:
        return self.authorization_id is not None and self.authorization.active

    class Meta:
        verbose_name = "Storage Account"
        verbose_name_plural = "Storage Accounts"

    def __str__(self):
        return f"{self.user.username} → {self.ledger_account.code}"


class Bucket(models.Model):
    name = models.CharField(max_length=100, unique=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    slug = models.SlugField(max_length=120, unique=True)
    storage_quota_mb = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Storage quota per user in MB. Leave blank for unlimited.",
    )
    tariff = models.ForeignKey(
        "tariffs.Tariff",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="buckets",
        help_text="Billing tariff for this bucket. Defaults to FILE-STORAGE when blank.",
    )

    class Meta:
        verbose_name = "Bucket"
        verbose_name_plural = "Buckets"
        unique_together = ('name', 'owner')

    def __str__(self):
        return f"Bucket {self.name}"


class BucketBilling(models.Model):
    """
    Billing profile for a Bucket. One per bucket; created by admins.
    Defines allowed payment currencies and their exchange rates for
    purchasing storage tokens, plus optional quota / tariff overrides.
    """
    bucket = models.OneToOneField(
        Bucket,
        on_delete=models.CASCADE,
        related_name="billing",
    )
    storage_quota_mb = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Per-user storage quota in MB. Overrides the bucket default when set.",
    )
    tariff = models.ForeignKey(
        "tariffs.Tariff",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="bucket_billings",
        help_text="Billing tariff override. Falls back to bucket tariff when blank.",
    )
    allowed_currencies = models.ManyToManyField(
        "assets.Asset",
        through="StorageTokenPrice",
        blank=True,
        related_name="bucket_billings",
        help_text="Assets accepted as payment for storage tokens.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Bucket Billing"
        verbose_name_plural = "Bucket Billings"

    def __str__(self):
        return f"Billing for {self.bucket.name}"

    def get_effective_quota(self):
        return self.storage_quota_mb if self.storage_quota_mb is not None else self.bucket.storage_quota_mb

    def get_effective_tariff(self):
        return self.tariff or self.bucket.tariff


class StorageTokenPrice(models.Model):
    """
    Exchange rate: how much `currency` asset the user pays per 1 STORAGE_TOKEN.
    Also specifies which ledger account receives the payment.
    """
    bucket_billing = models.ForeignKey(
        BucketBilling,
        on_delete=models.CASCADE,
        related_name="token_prices",
    )
    currency = models.ForeignKey(
        "assets.Asset",
        on_delete=models.CASCADE,
        related_name="storage_token_prices",
        help_text="Asset used to pay for storage tokens.",
    )
    price_per_token = models.DecimalField(
        max_digits=30, decimal_places=10,
        help_text="Cost of 1 STORAGE_TOKEN in this currency (e.g. 0.01 TUSD per token).",
    )
    revenue_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="storage_token_revenues",
        help_text="Ledger account that receives payments in this currency.",
    )

    class Meta:
        verbose_name = "Storage Token Price"
        verbose_name_plural = "Storage Token Prices"
        unique_together = ("bucket_billing", "currency")

    def __str__(self):
        return f"1 STORAGE_TOKEN = {self.price_per_token} {self.currency.unit_name}"


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
