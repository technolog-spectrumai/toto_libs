from django.db import models
from cryptography.fernet import Fernet
from PyPDF2 import PdfReader, PdfWriter
from django.contrib.auth.models import User
import os
from gervazy.models import KeyRing


# Abstract base model for files
class GeneralFile(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to='vault/files/')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_encrypted = models.BooleanField(default=False)
    is_public = models.BooleanField(default=False, help_text="If true, file is visible to others")
    notes = models.TextField(blank=True, null=True)


    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.title} ({self.owner.username})"

    def get_file_info(self):
        return {
            "title": self.title,
            "owner": self.owner.username,
            "encrypted": self.is_encrypted,
            "public": self.is_public,
            "uploaded": self.uploaded_at.strftime("%Y-%m-%d"),
            "notes": self.notes or "—"
        }


# VaultPdf model for encrypted PDFs
class VaultPdf(GeneralFile):
    class Meta:
        verbose_name = "Vault PDF"
        verbose_name_plural = "Vault PDFs"

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

    def decrypt_pdf(self, password: str):
        if not self.is_encrypted:
            raise ValueError("PDF is not encrypted.")

        input_path = self.file.path
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_decrypted{ext}"

        reader = PdfReader(input_path)

        if not reader.is_encrypted:
            raise ValueError("PDF file is not encrypted at the file level.")

        try:
            reader.decrypt(password)
        except Exception as e:
            raise ValueError(f"Failed to decrypt PDF: {str(e)}")

        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)

        with open(output_path, "wb") as f:
            writer.write(f)

        self.file.save(os.path.basename(output_path), open(output_path, "rb"))
        os.remove(output_path)
        self.is_encrypted = False
        self.save()

# VaultFile model for general encrypted files
class VaultFile(GeneralFile):
    class Meta:
        verbose_name = "Vault File"
        verbose_name_plural = "Vault Files"

    def encrypt_file(self, password: str):
        if self.is_encrypted:
            return

        input_path = self.file.path
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_enc{ext}"

        keyring = self.owner.keyrings.first()
        if not keyring:
            raise ValueError("No KeyRing associated with user.")

        key = keyring.derive_key(password)
        fernet = Fernet(key)

        with open(input_path, 'rb') as f:
            data = f.read()

        encrypted_data = fernet.encrypt(data)

        with open(output_path, 'wb') as f:
            f.write(encrypted_data)

        self.file.save(os.path.basename(output_path), open(output_path, 'rb'))
        os.remove(output_path)
        self.is_encrypted = True
        self.save()

    def decrypt_file(self, password: str):
        if not self.is_encrypted:
            raise ValueError("File is not encrypted.")

        input_path = self.file.path
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_dec{ext}"

        keyring = self.owner.keyrings.first()
        if not keyring:
            raise ValueError("No KeyRing associated with user.")

        key = keyring.derive_key(password)
        fernet = Fernet(key)

        try:
            with open(input_path, 'rb') as f:
                encrypted_data = f.read()

            decrypted_data = fernet.decrypt(encrypted_data)

            with open(output_path, 'wb') as f:
                f.write(decrypted_data)

            self.file.save(os.path.basename(output_path), open(output_path, 'rb'))
            os.remove(output_path)
            self.is_encrypted = False
            self.save()
        except Exception as e:
            raise ValueError(f"Decryption failed: {str(e)}")






