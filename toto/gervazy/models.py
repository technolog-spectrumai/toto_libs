import os
import uuid
import base64

from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


KEY_STATE_CHOICES = [
    ("pre_active", "Pre-active"),
    ("active", "Active"),
    ("suspended", "Suspended"),
    ("retired", "Retired"),
    ("compromised", "Compromised"),
    ("destroyed", "Destroyed"),
]


class UserVault(models.Model):
    """Per-user vault. Stores Argon2id KDF params and salt; never stores the key itself."""
    owner = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="uservaults"
    )
    name = models.CharField(max_length=128, unique=True)
    kdf = models.CharField(max_length=32, default="argon2id")
    kdf_version = models.PositiveSmallIntegerField(default=19)
    salt = models.BinaryField(help_text="Random 16-byte KDF salt", editable=False)
    argon2_memory_cost = models.PositiveIntegerField(default=65536)
    argon2_iterations = models.PositiveIntegerField(default=3)
    argon2_lanes = models.PositiveSmallIntegerField(default=4)
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.owner.username})"

    def save(self, *args, **kwargs):
        if not self.salt:
            self.salt = os.urandom(16)
        if not self.name:
            self.name = f"vault-{uuid.uuid4()}"
        super().save(*args, **kwargs)

    def derive_key(self, password: str) -> bytes:
        """Derive a 32-byte UKEK via Argon2id. Returns Fernet-compatible base64url bytes."""
        kdf = Argon2id(
            salt=bytes(self.salt),
            length=32,
            iterations=self.argon2_iterations,
            lanes=self.argon2_lanes,
            memory_cost=self.argon2_memory_cost,
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))


class VaultMasterKey(models.Model):
    """VMK encrypted by the password-derived UKEK. One active VMK per vault at a time."""
    vault = models.ForeignKey(
        UserVault, on_delete=models.CASCADE, related_name="master_keys"
    )
    encrypted_vmk = models.BinaryField()
    nonce = models.BinaryField()
    algorithm = models.CharField(max_length=32, default="AES-256-GCM")
    version = models.PositiveSmallIntegerField(default=1)
    state = models.CharField(max_length=20, choices=KEY_STATE_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-version"]

    def __str__(self):
        return f"VMK v{self.version} — {self.vault.name} [{self.state}]"


class WrappedDataKey(models.Model):
    """DEK wrapped by the VMK. Envelope layer between VMK and object-level secrets."""
    vault = models.ForeignKey(
        UserVault, on_delete=models.CASCADE, related_name="data_keys"
    )
    vmk_version = models.PositiveSmallIntegerField()
    encrypted_dek = models.BinaryField()
    nonce = models.BinaryField()
    algorithm = models.CharField(max_length=32, default="AES-256-GCM")
    version = models.PositiveSmallIntegerField(default=1)
    state = models.CharField(max_length=20, choices=KEY_STATE_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-version"]

    def __str__(self):
        return (
            f"DEK v{self.version} (vmk_v{self.vmk_version}) — {self.vault.name} [{self.state}]"
        )


class EncryptedSecret(models.Model):
    """AES-256-GCM encrypted secret with envelope key reference."""
    vault = models.ForeignKey(
        UserVault, on_delete=models.CASCADE, related_name="secrets"
    )
    wrapped_key = models.ForeignKey(
        WrappedDataKey, on_delete=models.PROTECT, related_name="secrets"
    )
    name = models.CharField(max_length=255)
    purpose = models.CharField(max_length=255, blank=True)
    ciphertext = models.BinaryField()
    nonce = models.BinaryField()
    aad = models.BinaryField(blank=True, default=b"")
    algorithm = models.CharField(max_length=32, default="AES-256-GCM")
    version = models.PositiveSmallIntegerField(default=1)
    state = models.CharField(max_length=20, choices=KEY_STATE_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"EncryptedSecret '{self.name}' [{self.state}]"

    def is_expired(self):
        return bool(self.expires_at and timezone.now() >= self.expires_at)


class EncryptedFile(models.Model):
    """Metadata for a chunked AES-256-GCM encrypted file."""
    vault = models.ForeignKey(
        UserVault, on_delete=models.CASCADE, related_name="encrypted_files"
    )
    owner = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="encrypted_files"
    )
    wrapped_key = models.ForeignKey(
        WrappedDataKey, on_delete=models.PROTECT, related_name="files"
    )
    original_name_encrypted = models.BinaryField()
    original_name_nonce = models.BinaryField()
    mime_type = models.CharField(max_length=127, blank=True)
    file = models.FileField(upload_to="vault/encrypted/")
    nonce_strategy = models.CharField(max_length=32, default="random_per_chunk")
    chunk_size = models.PositiveIntegerField(default=65536)
    chunk_count = models.PositiveIntegerField(default=0)
    ciphertext_sha256 = models.CharField(max_length=64, blank=True)
    plaintext_size = models.BigIntegerField(default=0)
    ciphertext_size = models.BigIntegerField(default=0)
    algorithm = models.CharField(max_length=32, default="AES-256-GCM")
    version = models.PositiveSmallIntegerField(default=1)
    state = models.CharField(max_length=20, choices=KEY_STATE_CHOICES, default="active")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"EncryptedFile #{self.pk} [{self.state}]"


class EncryptedFileChunk(models.Model):
    """Single chunk of an EncryptedFile with its own nonce."""
    encrypted_file = models.ForeignKey(
        EncryptedFile, on_delete=models.CASCADE, related_name="chunks"
    )
    index = models.PositiveIntegerField()
    nonce = models.BinaryField()
    ciphertext_size = models.PositiveIntegerField()
    ciphertext_sha256 = models.CharField(max_length=64, blank=True)

    class Meta:
        unique_together = [("encrypted_file", "index")]
        ordering = ["index"]

    def __str__(self):
        return f"Chunk {self.index} of EncryptedFile #{self.encrypted_file_id}"


class EncryptedPrivateKey(models.Model):
    """Ed25519/RSA private key encrypted at rest. Public key stored in plaintext."""
    KEY_TYPE_CHOICES = [
        ("Ed25519", "Ed25519"),
        ("RSA-2048", "RSA-2048"),
        ("RSA-4096", "RSA-4096"),
    ]
    vault = models.ForeignKey(
        UserVault, on_delete=models.CASCADE, related_name="private_keys"
    )
    wrapped_key = models.ForeignKey(
        WrappedDataKey, on_delete=models.PROTECT, related_name="private_keys"
    )
    key_id = models.CharField(max_length=100, unique=True)
    key_type = models.CharField(max_length=16, choices=KEY_TYPE_CHOICES)
    public_key_pem = models.TextField()
    issuer = models.URLField(blank=True)
    encrypted_private_key = models.BinaryField()
    nonce = models.BinaryField()
    aad = models.BinaryField(blank=True, default=b"")
    algorithm = models.CharField(max_length=32, default="AES-256-GCM")
    state = models.CharField(max_length=20, choices=KEY_STATE_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"EncryptedPrivateKey {self.key_id} ({self.key_type}) [{self.state}]"


class CryptoAuditLog(models.Model):
    """Append-only audit trail for cryptographic operations."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL, related_name="crypto_audit_logs"
    )
    vault = models.ForeignKey(
        UserVault, null=True, on_delete=models.SET_NULL, related_name="audit_logs"
    )
    action = models.CharField(max_length=64)
    object_type = models.CharField(max_length=64, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    success = models.BooleanField()
    reason = models.TextField(blank=True)
    key_version = models.PositiveSmallIntegerField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        status = "OK" if self.success else "FAIL"
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.action} by {self.actor} [{status}]"
