import os
import tempfile
from django.core.files import File
from vault.models import VaultFile


class ModelSerializer:
    def __init__(self, model_class):
        self.model_class = model_class

    def dump_queryset_to_bucket(self, queryset, owner, bucket, filename):
        """
        Serialize the queryset to JSON and store it in the vault as a VaultFile with file_type='jsonl'.
        """
        data = self.model_class.serialize_queryset(queryset)

        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, filename)

            # Write serialized JSON to temp file
            with open(json_path, "w") as f:
                f.write(data)

            # Create VaultFile with enforced file_type='jsonl'
            vault_file = VaultFile.objects.create(
                owner=owner,
                title=f"{filename} (dump)",
                file_type='json',
                bucket=bucket,
                file=File(open(json_path, "rb"), name=filename)
            )
            vault_file.content_hash = vault_file.create_hash()
            vault_file.save()

            return vault_file

    def load_from_bucket(self, vault_file):
        """
        Load JSONL data from a VaultFile and save it to the database.
        Raises ValueError if file_type is not 'jsonl'.
        """
        if vault_file.file_type != 'json':
            raise ValueError(f"Unsupported file type '{vault_file.file_type}'. Expected 'jsonl'.")

        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, os.path.basename(vault_file.file.name))

            # Write file content to temp file
            with open(json_path, "wb") as f:
                f.write(vault_file.file.read())

            # Deserialize and save objects
            with open(json_path, "r") as f:
                for obj in self.model_class.deserialize_data(f.read()):
                    obj.save()
