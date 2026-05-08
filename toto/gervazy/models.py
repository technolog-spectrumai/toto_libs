import os
import re
from django.contrib.auth.models import User
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric import padding
import uuid
import base64
import secrets
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from cryptography.fernet import Fernet



class KeyRing(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='keyrings')
    salt = models.BinaryField(help_text="Salt used for key derivation", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)
    name = models.CharField(
        max_length=128,
        unique=True,
        blank=True,
        help_text="Optional unique name for this KeyRing. Auto-generated if omitted."
    )

    def __str__(self):
        return f"{self.name} ({self.owner.username})"

    def regenerate_salt(self):
        self.salt = os.urandom(16)
        self.save()

    def get_summary(self):
        return {
            "name": self.name,
            "owner": self.owner.username,
            "created": self.created_at.strftime("%Y-%m-%d"),
            "notes": self.notes or "—",
        }

    def derive_key(self, password: str) -> bytes:
        """Derives a symmetric key from the given password and this KeyRing's salt."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self.salt,
            iterations=100_000,
            backend=default_backend()
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))


    def save(self, *args, **kwargs):
        if not self.salt:
            self.salt = os.urandom(16)
        if not self.name:
            self.name = f"keyring-{uuid.uuid4()}"
        super().save(*args, **kwargs)



# 🔐 RSA Key Pair Management
class RSAKeyPair(models.Model):
    key_id = models.CharField(max_length=100, unique=True)
    public_key_pem = models.TextField()
    private_key_pem = models.TextField()
    issuer = models.URLField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"RSAKeyPair {self.key_id}"

    def get_private_key(self):
        return serialization.load_pem_private_key(
            self.private_key_pem.encode(),
            password=None,
            backend=default_backend()
        )

    def get_public_key(self):
        return serialization.load_pem_public_key(
            self.public_key_pem.encode(),
            backend=default_backend()
        )

    @classmethod
    def generate(cls, key_id: str, issuer: str) -> "RSAKeyPair":
        private_key_obj = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048
        )

        private_pem = private_key_obj.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ).decode()

        public_pem = private_key_obj.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()

        return cls(
            key_id=key_id,
            issuer=issuer,
            private_key_pem=private_pem,
            public_key_pem=public_pem
        )

    def sign(self, data: bytes) -> bytes:
        """
        Sign arbitrary data using the private key.
        Returns the signature as raw bytes.
        """
        private_key = self.get_private_key()
        signature = private_key.sign(
            data,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        return signature

    def verify(self, data: bytes, signature: bytes) -> bool:
        """
        Verify a signature using the public key.
        Returns True if valid, False otherwise.
        """
        public_key = self.get_public_key()
        try:
            public_key.verify(
                signature,
                data,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )
            return True
        except Exception:
            return False

    def save(self, *args, **kwargs):
        """
        Ensure that when saving, if no PEMs are present,
        generate a fresh keypair automatically.
        """
        if not self.private_key_pem or not self.public_key_pem:
            new_pair = RSAKeyPair.generate(self.key_id, self.issuer)
            self.private_key_pem = new_pair.private_key_pem
            self.public_key_pem = new_pair.public_key_pem
        super().save(*args, **kwargs)


class SecretKey(models.Model):
    SIZE_CHOICES = [
        (64, "64 bytes (~86 chars)"),
        (128, "128 bytes (~172 chars)"),
        (256, "256 bytes (~344 chars)"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    keyring = models.ForeignKey(KeyRing, on_delete=models.CASCADE, related_name="secrets")

    key_encrypted = models.CharField(max_length=512)
    size = models.PositiveIntegerField(choices=SIZE_CHOICES, default=64)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)

    # --- Key Derivation ---
    def _derive_key(self, passphrase: str) -> bytes:
        return self.keyring.derive_key(passphrase)

    def _get_fernet(self, passphrase: str) -> Fernet:
        return Fernet(self._derive_key(passphrase))

    # --- Public API ---
    def set_key(self, raw_key: str, passphrase: str):
        f = self._get_fernet(passphrase)
        self.key_encrypted = f.encrypt(raw_key.encode()).decode()

    def get_key(self, passphrase: str) -> str:
        f = self._get_fernet(passphrase)
        return f.decrypt(self.key_encrypted.encode()).decode()

    def rotate(self, passphrase: str):
        new_key = secrets.token_urlsafe(self.size)
        self.set_key(new_key, passphrase)
        self.created_at = timezone.now()
        self.save(update_fields=["key_encrypted", "created_at"])

    def is_expired(self):
        return self.expires_at and timezone.now() >= self.expires_at

    def save(self, *args, **kwargs):
        if not self.key_encrypted:
            raise ValueError("Call set_key(raw_key, passphrase) before saving.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"SecretKey {self.id} (size={self.size})"


class EnvironmentVariable(models.Model):
    """Admin-managed environment variable for the current Django process."""

    name = models.CharField(
        max_length=120,
        unique=True,
        help_text="Environment variable name, for example OPENAI_API_KEY.",
    )
    active = models.BooleanField(
        default=True,
        help_text="When active, admin can set the value into os.environ for this process.",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", self.name or ""):
            raise ValidationError({
                "name": "Use a shell-safe environment variable name, like OPENAI_API_KEY."
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def apply_to_environment(self):
        if not self.active:
            os.environ.pop(self.name, None)

    def delete(self, *args, **kwargs):
        os.environ.pop(self.name, None)
        return super().delete(*args, **kwargs)

    def set_value(self, value):
        if self.active:
            os.environ[self.name] = value
            return

        os.environ.pop(self.name, None)

    @property
    def value(self):
        if not self.active:
            return ""

        return os.environ.get(self.name, "")

    @property
    def masked_value(self):
        value = self.value
        if not value:
            return ""

        if len(value) <= 8:
            return "*" * len(value)

        return f"{value[:4]}{'*' * 8}{value[-4:]}"


class SecretPassword(models.Model):
    """
    Binds a SecretKey to the EnvironmentVariable that unlocks it.

    Despite the historical name, this model does not store a password payload.
    It is an automation box for resolving a SecretKey without retyping the
    passphrase in admin or service code.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Human-friendly identifier (unique)
    name = models.CharField(
        max_length=128,
        unique=True,
        blank=True,
        help_text="Optional name for this secret access box. Auto-generated if omitted."
    )

    secret_key = models.ForeignKey(
        SecretKey,
        on_delete=models.CASCADE,
        related_name="passwords",
        null=True,
        blank=True,
        help_text="SecretKey used to encrypt and decrypt this password."
    )

    environment_variable = models.ForeignKey(
        "gervazy.EnvironmentVariable",
        on_delete=models.PROTECT,
        related_name="secret_passwords",
        null=True,
        blank=True,
        help_text="Environment variable that contains the passphrase for unlocking the SecretKey."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)

    # --- Unlock / encryption helpers ---
    def _get_unlock_passphrase(self, passphrase: str | None = None) -> str:
        if passphrase:
            return passphrase

        if not self.environment_variable_id:
            raise RuntimeError(f"SecretPassword {self.name} has no unlock environment variable configured.")

        env_name = self.environment_variable.name
        resolved = os.environ.get(env_name)
        if resolved:
            return resolved

        if self.environment_variable.value:
            return self.environment_variable.value

        raise RuntimeError(f"Environment variable {env_name} must be set to unlock {self.name}.")

    def get_passphrase(self, passphrase: str | None = None) -> str:
        return self._get_unlock_passphrase(passphrase)

    def get_secret_key(self, passphrase: str | None = None) -> str:
        """Return the decrypted SecretKey value using the configured env var."""
        if not self.secret_key_id:
            raise RuntimeError(f"SecretPassword {self.name} has no SecretKey configured.")

        return self.secret_key.get_key(self._get_unlock_passphrase(passphrase))

    def rotate_secret_key(self, passphrase: str | None = None):
        if not self.secret_key_id:
            raise RuntimeError(f"SecretPassword {self.name} has no SecretKey configured.")

        self.secret_key.rotate(self._get_unlock_passphrase(passphrase))

    def is_expired(self):
        return self.expires_at and timezone.now() >= self.expires_at

    def save(self, *args, **kwargs):
        if not self.secret_key_id:
            raise ValueError("SecretPassword requires a SecretKey.")

        if not self.environment_variable_id:
            raise ValueError("SecretPassword requires an EnvironmentVariable.")

        # Auto-generate unique name if missing
        if not self.name:
            self.name = f"password-{uuid.uuid4()}"

        super().save(*args, **kwargs)

    def __str__(self):
        return f"SecretPassword {self.name}"
