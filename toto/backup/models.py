from django.db import models


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
