import os
from django.contrib.auth.models import User
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric import padding
import uuid
import secrets
from django.db import models
from django.utils import timezone


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
    """
    Secure storage for cryptographic secrets used in federation authentication.
    """

    SIZE_CHOICES = [
        (64, "64 bytes (~86 chars)"),
        (128, "128 bytes (~172 chars)"),
        (256, "256 bytes (~344 chars)"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(
        max_length=512,
        help_text="Secret key material (e.g., JWT signing secret)"
    )
    size = models.PositiveIntegerField(
        choices=SIZE_CHOICES,
        default=64,
        help_text="Entropy size used when generating the secret"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    passphrase = models.CharField(
        max_length=128,
        help_text="Passphrase required to reveal or rotate this secret in admin"
    )

    def __str__(self):
        return f"SecretKey {self.id} (size={self.size})"

    def rotate(self):
        """
        Rotate the secret key using the stored size.
        """
        self.key = secrets.token_urlsafe(self.size)
        self.created_at = timezone.now()
        self.save(update_fields=["key", "created_at"])

    def is_expired(self) -> bool:
        """
        Check if the secret has expired.
        """
        return self.expires_at and timezone.now() >= self.expires_at

    def save(self, *args, **kwargs):
        """
        Ensure a key is generated when creating a new SecretKey.
        """
        if not self.key:  # if empty, generate automatically
            self.key = secrets.token_urlsafe(self.size)
        super().save(*args, **kwargs)

