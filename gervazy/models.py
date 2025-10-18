from django.db import models
from cryptography.hazmat.primitives import hashes
import os
from django.contrib.auth.models import User
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
from cryptography.hazmat.backends import default_backend



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


