# client.py
from vault.models import VaultFile, Bucket
from vault.file_helper import FileHelper


class VaultClient:
    """
    JSON-returning wrapper around FileHelper.
    FileHelper handles:
      - bucket resolution
      - unique key generation
      - hashing
      - saving files
      - CRUD
    VaultClient only serializes results to JSON.
    """

    def __init__(self, bucket_name: str):
        self.helper = FileHelper(bucket_name)
        self.bucket = self.helper.bucket
        self.owner = self.helper.owner

    # ---------------------------------------------------------
    # JSON serializer
    # ---------------------------------------------------------

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
    # Create (upload) — delegates to FileHelper
    # ---------------------------------------------------------

    def upload_file(self, uploaded_file) -> dict:
        file = self.helper.save_file(uploaded_file)
        return self._file_to_json(file)

    # ---------------------------------------------------------
    # Read
    # ---------------------------------------------------------

    def get_file(self, key: str) -> dict:
        file = self.helper.get_file(key)
        if not file:
            return {"error": f"File with key '{key}' not found"}
        return self._file_to_json(file)

    def list_files(self) -> dict:
        files = self.helper.list_files()
        return {
            "bucket": self.bucket.name,
            "files": [self._file_to_json(f) for f in files]
        }

    # ---------------------------------------------------------
    # Update
    # ---------------------------------------------------------

    def update_file(self, key: str, new_file_content) -> dict:
        file = self.helper.update_file(key, new_file_content)
        if not file:
            return {"error": f"File with key '{key}' not found"}
        return self._file_to_json(file)

    # ---------------------------------------------------------
    # Delete
    # ---------------------------------------------------------

    def delete_file(self, key: str) -> dict:
        ok = self.helper.delete_file(key)
        if not ok:
            return {"error": f"File with key '{key}' not found"}
        return {"status": "deleted", "key": key}
