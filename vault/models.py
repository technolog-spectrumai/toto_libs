from django.db import models
from django.contrib.auth.models import User
from PyPDF2 import PdfReader, PdfWriter
import os


class KeyRing(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='keyrings')
    label = models.CharField(max_length=100)
    salt = models.BinaryField(help_text="Salt used for key derivation", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.label} ({self.owner.username})"

    def regenerate_salt(self):
        import os
        self.salt = os.urandom(16)
        self.save()

    def get_summary(self):
        return {
            "label": self.label,
            "owner": self.owner.username,
            "created": self.created_at.strftime("%Y-%m-%d"),
            "notes": self.notes or "—"
        }


class VaultPdf(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='vault_pdfs')
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to='vault/encrypted_pdfs/')
    keyring = models.ForeignKey(KeyRing, on_delete=models.SET_NULL, null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_encrypted = models.BooleanField(default=True)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.title} (PDF)"

    def encrypt_pdf(self, user_password: str, owner_password: str = None):
        if not owner_password:
            owner_password = user_password

        input_path = self.file.path
        output_path = os.path.splitext(input_path)[0] + "_encrypted.pdf"

        reader = PdfReader(input_path)
        writer = PdfWriter()

        for page in reader.pages:
            writer.add_page(page)

        writer.encrypt(user_password=user_password, owner_password=owner_password, use_128bit=True)

        with open(output_path, "wb") as f:
            writer.write(f)

        self.file.save(os.path.basename(output_path), open(output_path, "rb"))
        os.remove(output_path)
        self.is_encrypted = True
        self.save()

    def get_file_info(self):
        return {
            "title": self.title,
            "owner": self.owner.username,
            "encrypted": self.is_encrypted,
            "uploaded": self.uploaded_at.strftime("%Y-%m-%d"),
            "notes": self.notes or "—"
        }
