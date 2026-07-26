import secrets
import uuid

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.urls import reverse
from django.utils import timezone


def stored_backup_upload_to(instance, filename):
    return f"stored-backups/{instance.uid}/{filename}"


class BackupProfile(models.Model):
    """Backup signing keys for a Platform. Created on demand."""

    platform = models.OneToOneField(
        "core.Platform",
        on_delete=models.CASCADE,
        related_name="backup_profile",
    )
    signing_key = models.ForeignKey(
        "gervazy.EncryptedPrivateKey",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="backup_profiles",
        help_text="Private key used to sign outbound backup packages.",
    )
    verify_key = models.TextField(
        null=True,
        blank=True,
        help_text="Public key PEM used to verify incoming backup signatures.",
    )

    def __str__(self):
        return f"BackupProfile for {self.platform}"


class StoredBackup(models.Model):
    """A ZIP backup stored for remote API pull."""

    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    platform = models.ForeignKey(
        "core.Platform",
        on_delete=models.CASCADE,
        related_name="stored_backups",
    )
    name = models.CharField(max_length=180)
    file = models.FileField(upload_to=stored_backup_upload_to)
    apps = models.JSONField(default=list, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)

    magic_token = models.CharField(
        max_length=128,
        unique=True,
        default=secrets.token_urlsafe,
        help_text="High-entropy URL token required before API-key auth is checked.",
    )
    api_key_hash = models.CharField(max_length=255, blank=True)
    api_key_hint = models.CharField(
        max_length=16,
        blank=True,
        help_text="Last characters of the generated API key. The full key is shown only once.",
    )

    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stored_backups_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_pulled_at = models.DateTimeField(null=True, blank=True)
    pull_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def issue_api_key(self, raw_key=None):
        raw_key = raw_key or secrets.token_urlsafe(48)
        self.api_key_hash = make_password(raw_key)
        self.api_key_hint = raw_key[-8:]
        return raw_key

    def verify_api_key(self, raw_key):
        if not raw_key or not self.api_key_hash:
            return False
        return check_password(raw_key, self.api_key_hash)

    @property
    def is_expired(self):
        return bool(self.expires_at and timezone.now() >= self.expires_at)

    @property
    def can_be_pulled(self):
        return self.is_active and not self.is_expired and bool(self.file)

    def pull_path(self):
        return reverse(
            "backup:stored_backup_stream",
            kwargs={"uid": self.uid, "magic_token": self.magic_token},
        )

    def pull_url(self, request=None):
        path = self.pull_path()
        if request is not None:
            return request.build_absolute_uri(path)
        return path
