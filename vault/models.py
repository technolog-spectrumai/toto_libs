from django.db import models
from django.contrib.auth.models import User
from gervazy.models import KeyRing
from vault.strategy.pdf import PdfStrategy
from vault.strategy.image import ImageStrategy
from vault.strategy.text import TextStrategy


class Bucket(models.Model):
    name = models.CharField(max_length=100)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        verbose_name = "Bucket"
        verbose_name_plural = "Buckets"
        unique_together = ('name', 'owner')

    def __str__(self):
        return self.name


class VaultFile(models.Model):
    FILE_TYPES = [
        ('pdf', 'PDF'),
        ('image', 'Image'),
        ('html', 'HTML'),
        ('text', 'Text File'),
    ]

    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to='vault/files/')
    file_type = models.CharField(max_length=10, choices=FILE_TYPES)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_encrypted = models.BooleanField(default=False)
    is_public = models.BooleanField(default=False, help_text="If true, file is visible to others")
    notes = models.TextField(blank=True, null=True)
    bucket = models.ForeignKey(Bucket, on_delete=models.SET_NULL, null=True, blank=True, related_name='files')

    class Meta:
        verbose_name = "Vault File"
        verbose_name_plural = "Vault Files"

    def __str__(self):
        return f"{self.title} ({self.owner.username})"

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
        elif self.file_type == 'image':
            return ImageStrategy()
        else:
            return TextStrategy()

    def encrypt(self, password: str, owner_password=None):
        if self.is_encrypted:
            return
        strategy = self.get_strategy()
        if self.file_type == 'pdf':
            if not owner_password:
                owner_password = password
        strategy.encrypt(self, password=password, owner_password=owner_password)

    def decrypt(self, password: str):
        if not self.is_encrypted:
            raise ValueError("File is not encrypted.")
        strategy = self.get_strategy()
        strategy.decrypt(self, password=password)
