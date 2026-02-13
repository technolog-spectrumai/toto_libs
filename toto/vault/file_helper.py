# file_helper.py
import random
import string
from django.utils.text import slugify
from vault.models import VaultFile, Bucket


class FileHelper:
    """
    Unified file manager for VaultFile.
    Handles:
      - bucket resolution
      - CRUD operations
      - unique key generation
      - short hashing
      - saving files
    """

    def __init__(self, bucket_name: str):
        self.bucket = self._resolve_bucket(bucket_name)
        self.owner = self.bucket.owner

    # ---------------------------------------------------------
    # Bucket + existence helpers
    # ---------------------------------------------------------

    def _resolve_bucket(self, bucket_name: str) -> Bucket:
        try:
            return Bucket.objects.get(name=bucket_name)
        except Bucket.DoesNotExist:
            raise Bucket.DoesNotExist(f"Bucket '{bucket_name}' does not exist")

    def file_exists(self, key: str) -> bool:
        return VaultFile.objects.filter(
            key=key,
            owner=self.owner,
            bucket=self.bucket
        ).exists()

    # ---------------------------------------------------------
    # Key generation
    # ---------------------------------------------------------

    @staticmethod
    def _short_hash(length=3):
        return ''.join(random.choices(string.ascii_lowercase, k=length))

    def generate_unique_key(self, uploaded_file) -> str:
        base_name = uploaded_file.name
        base_key = slugify(base_name)

        key = f"{base_key}-{self._short_hash()}"

        while self.file_exists(key):
            key = f"{base_key}-{self._short_hash()}"

        return key

    # ---------------------------------------------------------
    # Create (upload)
    # ---------------------------------------------------------

    def save_file(self, uploaded_file):
        """
        Generate a unique key and save the file.
        Returns the VaultFile instance.
        """
        key = self.generate_unique_key(uploaded_file)

        file = VaultFile.objects.create(
            owner=self.owner,
            title=uploaded_file.name,
            key=key,
            file=uploaded_file,
            bucket=self.bucket,
        )

        return file

    # ---------------------------------------------------------
    # Read
    # ---------------------------------------------------------

    def get_file(self, key: str):
        return VaultFile.objects.filter(
            key=key,
            owner=self.owner,
            bucket=self.bucket
        ).first()

    def list_files(self):
        return VaultFile.objects.filter(
            owner=self.owner,
            bucket=self.bucket
        )

    # ---------------------------------------------------------
    # Update
    # ---------------------------------------------------------

    def update_file(self, key: str, new_file_content):
        file = self.get_file(key)
        if not file:
            return None

        file.file = new_file_content
        file.save()
        return file

    # ---------------------------------------------------------
    # Delete
    # ---------------------------------------------------------

    def delete_file(self, key: str):
        file = self.get_file(key)
        if not file:
            return False

        file.delete()
        return True
