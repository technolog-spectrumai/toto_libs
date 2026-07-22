import os
import uuid
import base64

from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
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

AES_GCM_ALGORITHM = "AES-256-GCM"
ARGON2ID_KDF = "argon2id"


def random_12_byte_nonce() -> bytes:
    """Recommended nonce size for AES-GCM. Never reuse with the same key."""
    return os.urandom(12)


def random_16_byte_salt() -> bytes:
    return os.urandom(16)


class UserStrongbox(models.Model):
    """
    Per-user strongbox.

    Stores Argon2id KDF parameters and salt.
    Does NOT store the user's strongbox password or derived key.
    """

    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="user_strongboxes",
    )

    name = models.CharField(max_length=128)

    kdf = models.CharField(max_length=32, default=ARGON2ID_KDF)
    kdf_version = models.PositiveSmallIntegerField(default=19)

    salt = models.BinaryField(
        default=random_16_byte_salt,
        editable=False,
        help_text="Random 16-byte KDF salt.",
    )

    argon2_memory_cost = models.PositiveIntegerField(
        default=65536,
        help_text="Argon2id memory cost in KiB.",
    )
    argon2_iterations = models.PositiveIntegerField(default=3)
    argon2_lanes = models.PositiveSmallIntegerField(default=4)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"],
                name="unique_strongbox_name_per_owner",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.owner.username})"

    def clean(self):
        if self.kdf != ARGON2ID_KDF:
            raise ValidationError("Only argon2id is currently supported.")

        if len(bytes(self.salt)) != 16:
            raise ValidationError("Strongbox salt must be exactly 16 bytes.")

        if self.argon2_memory_cost < 19456:
            raise ValidationError("Argon2id memory cost is too low.")

        if self.argon2_iterations < 2:
            raise ValidationError("Argon2id iterations must be at least 2.")

        if self.argon2_lanes < 1:
            raise ValidationError("Argon2id lanes must be at least 1.")

    def save(self, *args, **kwargs):
        if not self.name:
            self.name = f"strongbox-{uuid.uuid4()}"

        self.full_clean()
        super().save(*args, **kwargs)

    def derive_key(self, password: str) -> bytes:
        """
        Derive a 32-byte UKEK from the user's strongbox password.

        Returns base64url-encoded bytes. crypto.py decodes this back to raw
        32 bytes before using it as an AES-256-GCM key.
        """
        if not password:
            raise ValueError("Strongbox password is required.")

        kdf = Argon2id(
            salt=bytes(self.salt),
            length=32,
            iterations=self.argon2_iterations,
            lanes=self.argon2_lanes,
            memory_cost=self.argon2_memory_cost,
        )

        return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


UserVault = UserStrongbox


class VaultMasterKey(models.Model):
    """
    VMK encrypted by the password-derived UKEK.

    One active VMK per strongbox should exist at a time.
    Application code should enforce activation and rotation workflows.
    """

    strongbox = models.ForeignKey(
        UserStrongbox,
        on_delete=models.CASCADE,
        related_name="master_keys",
    )

    encrypted_vmk = models.BinaryField()
    nonce = models.BinaryField(default=random_12_byte_nonce)

    algorithm = models.CharField(max_length=32, default=AES_GCM_ALGORITHM)
    version = models.PositiveSmallIntegerField(default=1)

    state = models.CharField(
        max_length=20,
        choices=KEY_STATE_CHOICES,
        default="active",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["strongbox", "version"],
                name="unique_vmk_version_per_strongbox",
            ),
            models.UniqueConstraint(
                fields=["strongbox", "nonce"],
                name="unique_vmk_nonce_per_strongbox",
            ),
        ]

    def __str__(self):
        return f"VMK v{self.version} — {self.strongbox.name} [{self.state}]"

    def clean(self):
        if self.algorithm != AES_GCM_ALGORITHM:
            raise ValidationError("Only AES-256-GCM is currently supported.")

        if len(bytes(self.nonce)) != 12:
            raise ValidationError("AES-GCM nonce must be exactly 12 bytes.")

        if self.state in {"retired", "compromised", "destroyed"} and not self.retired_at:
            self.retired_at = timezone.now()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class WrappedDataKey(models.Model):
    """
    DEK encrypted/wrapped by a VaultMasterKey.

    DEKs encrypt actual objects such as secrets, files, and private keys.
    """

    strongbox = models.ForeignKey(
        UserStrongbox,
        on_delete=models.CASCADE,
        related_name="data_keys",
    )

    vmk = models.ForeignKey(
        VaultMasterKey,
        on_delete=models.PROTECT,
        related_name="wrapped_data_keys",
    )

    encrypted_dek = models.BinaryField()
    nonce = models.BinaryField(default=random_12_byte_nonce)

    algorithm = models.CharField(max_length=32, default=AES_GCM_ALGORITHM)
    version = models.PositiveSmallIntegerField(default=1)

    state = models.CharField(
        max_length=20,
        choices=KEY_STATE_CHOICES,
        default="active",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["strongbox", "version"],
                name="unique_dek_version_per_strongbox",
            ),
            models.UniqueConstraint(
                fields=["vmk", "nonce"],
                name="unique_dek_nonce_per_vmk",
            ),
        ]

    def __str__(self):
        return f"DEK v{self.version} — {self.strongbox.name} [{self.state}]"

    def clean(self):
        if self.algorithm != AES_GCM_ALGORITHM:
            raise ValidationError("Only AES-256-GCM is currently supported.")

        if self.vmk_id and self.strongbox_id and self.vmk.strongbox_id != self.strongbox_id:
            raise ValidationError("WrappedDataKey strongbox must match VMK strongbox.")

        if self.vmk_id and self.vmk.state != "active":
            raise ValidationError("Cannot create or use a DEK under a non-active VMK.")

        if len(bytes(self.nonce)) != 12:
            raise ValidationError("AES-GCM nonce must be exactly 12 bytes.")

        if self.state in {"retired", "compromised", "destroyed"} and not self.rotated_at:
            self.rotated_at = timezone.now()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class EncryptedSecret(models.Model):
    """
    AES-256-GCM encrypted secret.

    Example use: SMTP password, API token, OAuth client secret, webhook secret.
    This model stores encrypted bytes only. Never store plaintext here.
    """

    strongbox = models.ForeignKey(
        UserStrongbox,
        on_delete=models.CASCADE,
        related_name="secrets",
    )

    wrapped_key = models.ForeignKey(
        WrappedDataKey,
        on_delete=models.PROTECT,
        related_name="secrets",
    )

    name = models.CharField(max_length=255)

    purpose = models.CharField(
        max_length=255,
        blank=True,
        help_text="Example: smtp_password, api_token, oauth_client_secret.",
    )

    ciphertext = models.BinaryField()

    nonce = models.BinaryField(
        default=random_12_byte_nonce,
        help_text="Random 12-byte AES-GCM nonce. Must be unique per wrapped_key.",
    )

    aad = models.BinaryField(
        blank=True,
        default=b"",
        help_text="Additional authenticated data. Bind ciphertext to context.",
    )

    algorithm = models.CharField(max_length=32, default=AES_GCM_ALGORITHM)
    version = models.PositiveSmallIntegerField(default=1)

    state = models.CharField(
        max_length=20,
        choices=KEY_STATE_CHOICES,
        default="active",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    expires_at = models.DateTimeField(null=True, blank=True)
    rotated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["strongbox", "name"],
                name="unique_secret_name_per_strongbox",
            ),
            models.UniqueConstraint(
                fields=["wrapped_key", "nonce"],
                name="unique_secret_nonce_per_wrapped_key",
            ),
        ]

    def __str__(self):
        return f"EncryptedSecret '{self.name}' [{self.state}]"

    def clean(self):
        if self.algorithm != AES_GCM_ALGORITHM:
            raise ValidationError("Only AES-256-GCM is currently supported.")

        if self.wrapped_key_id and self.strongbox_id and self.wrapped_key.strongbox_id != self.strongbox_id:
            raise ValidationError("EncryptedSecret strongbox must match wrapped key strongbox.")

        if self.wrapped_key_id and self.wrapped_key.state != "active":
            raise ValidationError("Cannot encrypt new secrets using a non-active DEK.")

        if len(bytes(self.nonce)) != 12:
            raise ValidationError("AES-GCM nonce must be exactly 12 bytes.")

        if not self.ciphertext:
            raise ValidationError("Ciphertext cannot be empty.")

        if self.expires_at and self.created_at and self.expires_at <= self.created_at:
            raise ValidationError("expires_at must be later than created_at.")

        if self.state in {"retired", "compromised", "destroyed"} and not self.rotated_at:
            self.rotated_at = timezone.now()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def is_expired(self):
        return bool(self.expires_at and timezone.now() >= self.expires_at)

    def can_decrypt(self):
        return (
            self.state == "active"
            and self.wrapped_key.state == "active"
            and self.wrapped_key.vmk.state == "active"
            and not self.is_expired()
        )

    def build_aad(self) -> bytes:
        """
        Stable AAD recommendation.

        Do not include mutable fields like name unless you re-encrypt after rename.
        """
        return f"EncryptedSecret:{self.id}:{self.strongbox_id}:v{self.version}".encode("utf-8")


class EncryptedFile(models.Model):
    """
    Metadata for a chunked AES-256-GCM encrypted file.

    Each chunk must have a unique nonce under the same encrypted file.
    """

    strongbox = models.ForeignKey(
        UserStrongbox,
        on_delete=models.CASCADE,
        related_name="encrypted_files",
    )

    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="encrypted_files",
    )

    wrapped_key = models.ForeignKey(
        WrappedDataKey,
        on_delete=models.PROTECT,
        related_name="files",
    )

    original_name_encrypted = models.BinaryField()
    original_name_nonce = models.BinaryField(default=random_12_byte_nonce)

    mime_type = models.CharField(max_length=127, blank=True)
    file = models.FileField(upload_to="strongbox/encrypted/")

    nonce_strategy = models.CharField(max_length=32, default="random_per_chunk")

    chunk_size = models.PositiveIntegerField(default=65536)
    chunk_count = models.PositiveIntegerField(default=0)

    ciphertext_sha256 = models.CharField(max_length=64, blank=True)

    plaintext_size = models.BigIntegerField(default=0)
    ciphertext_size = models.BigIntegerField(default=0)

    algorithm = models.CharField(max_length=32, default=AES_GCM_ALGORITHM)
    version = models.PositiveSmallIntegerField(default=1)

    state = models.CharField(
        max_length=20,
        choices=KEY_STATE_CHOICES,
        default="active",
    )

    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["wrapped_key", "original_name_nonce"],
                name="unique_file_name_nonce_per_wrapped_key",
            ),
        ]

    def __str__(self):
        return f"EncryptedFile {self.id} [{self.state}]"

    def clean(self):
        if self.algorithm != AES_GCM_ALGORITHM:
            raise ValidationError("Only AES-256-GCM is currently supported.")

        if self.wrapped_key_id and self.strongbox_id and self.wrapped_key.strongbox_id != self.strongbox_id:
            raise ValidationError("EncryptedFile strongbox must match wrapped key strongbox.")

        if len(bytes(self.original_name_nonce)) != 12:
            raise ValidationError("AES-GCM nonce must be exactly 12 bytes.")

        if self.chunk_size < 4096:
            raise ValidationError("Chunk size is too small.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class EncryptedFileChunk(models.Model):
    """Single encrypted chunk of an EncryptedFile."""

    encrypted_file = models.ForeignKey(
        EncryptedFile,
        on_delete=models.CASCADE,
        related_name="chunks",
    )

    index = models.PositiveIntegerField()
    nonce = models.BinaryField(default=random_12_byte_nonce)

    ciphertext_size = models.PositiveIntegerField()
    ciphertext_sha256 = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["index"]
        constraints = [
            models.UniqueConstraint(
                fields=["encrypted_file", "index"],
                name="unique_chunk_index_per_file",
            ),
            models.UniqueConstraint(
                fields=["encrypted_file", "nonce"],
                name="unique_chunk_nonce_per_file",
            ),
        ]

    def __str__(self):
        return f"Chunk {self.index} of EncryptedFile {self.encrypted_file_id}"

    def clean(self):
        if len(bytes(self.nonce)) != 12:
            raise ValidationError("AES-GCM nonce must be exactly 12 bytes.")

        if self.ciphertext_size <= 0:
            raise ValidationError("ciphertext_size must be positive.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class EncryptedPrivateKey(models.Model):
    """
    Encrypted private key. Public key is safe to store in plaintext.
    """

    KEY_TYPE_CHOICES = [
        ("Ed25519", "Ed25519"),
        ("RSA-2048", "RSA-2048"),
        ("RSA-4096", "RSA-4096"),
    ]

    strongbox = models.ForeignKey(
        UserStrongbox,
        on_delete=models.CASCADE,
        related_name="private_keys",
    )

    wrapped_key = models.ForeignKey(
        WrappedDataKey,
        on_delete=models.PROTECT,
        related_name="private_keys",
    )

    key_id = models.CharField(max_length=100, unique=True)
    key_type = models.CharField(max_length=16, choices=KEY_TYPE_CHOICES)

    public_key_pem = models.TextField()
    issuer = models.URLField(blank=True)

    encrypted_private_key = models.BinaryField()
    nonce = models.BinaryField(default=random_12_byte_nonce)
    aad = models.BinaryField(blank=True, default=b"")

    algorithm = models.CharField(max_length=32, default=AES_GCM_ALGORITHM)

    state = models.CharField(
        max_length=20,
        choices=KEY_STATE_CHOICES,
        default="active",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["key_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["wrapped_key", "nonce"],
                name="unique_private_key_nonce_per_wrapped_key",
            ),
        ]

    def __str__(self):
        return f"EncryptedPrivateKey {self.key_id} ({self.key_type}) [{self.state}]"

    def clean(self):
        if self.algorithm != AES_GCM_ALGORITHM:
            raise ValidationError("Only AES-256-GCM is currently supported.")

        if self.wrapped_key_id and self.strongbox_id and self.wrapped_key.strongbox_id != self.strongbox_id:
            raise ValidationError("Private key strongbox must match wrapped key strongbox.")

        if len(bytes(self.nonce)) != 12:
            raise ValidationError("AES-GCM nonce must be exactly 12 bytes.")

        if self.state in {"retired", "compromised", "destroyed"} and not self.retired_at:
            self.retired_at = timezone.now()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PersonSigningKey(models.Model):
    """
    Links a Person to their active Ed25519 signing key stored in gervazy.

    Each person has at most one active signing key at a time. When a new key
    is provisioned the old one is retired, not deleted — existing signatures
    remain verifiable against their archived public key.
    """

    person = models.ForeignKey(
        "people.Person",
        on_delete=models.CASCADE,
        related_name="signing_keys",
    )

    encrypted_private_key = models.ForeignKey(
        EncryptedPrivateKey,
        on_delete=models.PROTECT,
        related_name="person_signing_keys",
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        status = "active" if self.is_active else "retired"
        return f"SigningKey({self.person}) [{status}]"

    def retire(self):
        self.is_active = False
        self.retired_at = timezone.now()
        self.save(update_fields=["is_active", "retired_at"])


class CryptoAuditLog(models.Model):
    """
    Append-only audit trail for cryptographic operations.

    Never store plaintext secrets, passwords, decrypted keys, tokens, or exception
    messages containing sensitive material in this model.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    actor = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="crypto_audit_logs",
    )

    strongbox = models.ForeignKey(
        UserStrongbox,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )

    action = models.CharField(max_length=64)
    object_type = models.CharField(max_length=64, blank=True)
    object_id = models.CharField(max_length=64, blank=True)

    success = models.BooleanField()

    reason = models.TextField(
        blank=True,
        help_text="Do not store secret values here.",
    )

    key_version = models.PositiveSmallIntegerField(null=True, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        status = "OK" if self.success else "FAIL"
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.action} by {self.actor} [{status}]"

    def save(self, *args, **kwargs):
        forbidden_words = [
            "password=",
            "secret=",
            "token=",
            "private_key",
            "smtp_password",
        ]

        reason_lower = (self.reason or "").lower()

        if any(word in reason_lower for word in forbidden_words):
            raise ValidationError("CryptoAuditLog.reason appears to contain sensitive material.")

        super().save(*args, **kwargs)
