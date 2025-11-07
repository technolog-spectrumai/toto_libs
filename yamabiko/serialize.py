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
        Load JSON data from a VaultFile and save it to the database.
        Raises ValueError if file_type is not 'json' or if model mismatch occurs.
        """
        if vault_file.file_type != 'json':
            raise ValueError(f"Unsupported file type '{vault_file.file_type}'. Expected 'json'.")

        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, os.path.basename(vault_file.file.name))

            with open(json_path, "wb") as f:
                f.write(vault_file.file.read())

            with open(json_path, "r") as f:
                deserialized = self.model_class.deserialize_data(f.read())
                for obj in deserialized:
                    if not isinstance(obj.object, self.model_class):
                        raise ValueError(
                            f"Model mismatch: expected {self.model_class.__name__}, "
                            f"got {obj.object.__class__.__name__}"
                        )
                    obj.save()
