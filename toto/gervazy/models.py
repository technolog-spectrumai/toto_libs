import os
from django.contrib.auth.models import User
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric import padding
import uuid
import base64
import secrets
from django.db import models
from django.utils import timezone
from cryptography.fernet import Fernet



class KeyRing(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='keyrings')
    label = models.CharField(max_length=100)
    salt = models.BinaryField(help_text="Salt used for key derivation", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.label} ({self.owner.username})"

    def regenerate_salt(self):
        self.salt = os.urandom(16)
        self.save()

    def get_summary(self):
        return {
            "label": self.label,
            "owner": self.owner.username,
            "created": self.created_at.strftime("%Y-%m-%d"),
            "notes": self.notes or "—"
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


class SecretPassword(models.Model):
    """
    Secure storage for user-provided passwords of arbitrary length.
    Encrypted using a passphrase + KeyRing salt.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Human-friendly identifier (unique)
    name = models.CharField(
        max_length=128,
        unique=True,
        blank=True,
        help_text="Optional name for this password. Auto-generated if omitted."
    )

    keyring = models.ForeignKey(
        KeyRing,
        on_delete=models.CASCADE,
        related_name="passwords"
    )

    password_encrypted = models.TextField(help_text="Encrypted password material")

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)

    # --- Key Derivation ---
    def _derive_key(self, passphrase: str) -> bytes:
        return self.keyring.derive_key(passphrase)

    def _get_fernet(self, passphrase: str) -> Fernet:
        return Fernet(self._derive_key(passphrase))

    # --- Public API ---
    def set_password(self, raw_password: str, passphrase: str):
        """Encrypt and store the password."""
        f = self._get_fernet(passphrase)
        self.password_encrypted = f.encrypt(raw_password.encode()).decode()

    def get_password(self, passphrase: str) -> str:
        """Decrypt and return the password."""
        f = self._get_fernet(passphrase)
        return f.decrypt(self.password_encrypted.encode()).decode()

    def rotate(self, passphrase: str):
        """
        Re-encrypt the password with a new key derived from the same passphrase.
        Useful after KeyRing salt rotation.
        """
        raw = self.get_password(passphrase)
        self.set_password(raw, passphrase)
        self.created_at = timezone.now()
        self.save(update_fields=["password_encrypted", "created_at"])

    def is_expired(self):
        return self.expires_at and timezone.now() >= self.expires_at

    def save(self, *args, **kwargs):
        # Ensure password is set
        if not self.password_encrypted:
            raise ValueError("Call set_password(raw_password, passphrase) before saving.")

        # Auto-generate unique name if missing
        if not self.name:
            self.name = f"password-{uuid.uuid4()}"

        super().save(*args, **kwargs)

    def __str__(self):
        return f"SecretPassword {self.name}"


