# client.py
from django.utils.text import slugify
from vault.models import VaultFile, Bucket


class VaultClient:
    """
    Strict bucket-scoped CRUD client for VaultFile.
    Always returns JSON-serializable dicts.
    """

    def __init__(self, bucket_name: str):
        self.bucket = self._resolve_bucket(bucket_name)
        self.owner = self.bucket.owner

    # ---------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------
    def _resolve_bucket(self, bucket_name: str) -> Bucket:
        try:
            return Bucket.objects.get(name=bucket_name)
        except Bucket.DoesNotExist:
            raise Bucket.DoesNotExist(f"Bucket '{bucket_name}' does not exist")

    def _file_to_json(self, file: VaultFile) -> dict:
        return {
            "id": str(file.id),
            "title": file.title,
            "key": file.key,
            "file_url": file.file.url if file.file else None,
            "bucket": file.bucket.name if file.bucket else None,
            "uploaded_at": file.uploaded_at.isoformat() if file.uploaded_at else None,
            "is_public": file.is_public,
            "is_encrypted": file.is_encrypted,
        }

    # ---------------------------------------------------------
    # Create (upload)
    # ---------------------------------------------------------
    def upload_file(self, uploaded_file, name: str) -> dict:
        """
        Upload a file into this bucket.
        Returns JSON only.
        """

        key = slugify(name)

        file = VaultFile.objects.create(
            owner=self.owner,
            title=name,
            key=key,
            file=uploaded_file,
            bucket=self.bucket,
        )

        return self._file_to_json(file)

    # ---------------------------------------------------------
    # Read (by key)
    # ---------------------------------------------------------
    def get_file(self, key: str) -> dict:
        try:
            file = VaultFile.objects.get(
                key=key,
                owner=self.owner,
                bucket=self.bucket
            )
        except VaultFile.DoesNotExist:
            return {"error": f"File with key '{key}' not found"}

        return self._file_to_json(file)

    def list_files(self) -> dict:
        files = VaultFile.objects.filter(
            owner=self.owner,
            bucket=self.bucket
        )

        return {
            "bucket": self.bucket.name,
            "files": [self._file_to_json(f) for f in files]
        }

    # ---------------------------------------------------------
    # Update (content only)
    # ---------------------------------------------------------
    def update_file(self, key: str, new_file_content) -> dict:
        file = VaultFile.objects.filter(
            key=key,
            owner=self.owner,
            bucket=self.bucket
        ).first()

        if not file:
            return {"error": f"File with key '{key}' not found"}

        file.file = new_file_content
        file.save()

        return self._file_to_json(file)

    # ---------------------------------------------------------
    # Delete
    # ---------------------------------------------------------
    def delete_file(self, key: str) -> dict:
        file = VaultFile.objects.filter(
            key=key,
            owner=self.owner,
            bucket=self.bucket
        ).first()

        if not file:
            return {"error": f"File with key '{key}' not found"}

        file.delete()
        return {"status": "deleted", "key": key}
