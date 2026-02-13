import os
import tempfile
from django.core.files import File
from vault.models import VaultFile
from .models import SerializableModel


class ModelSerializer:
    def __init__(self, model_class):
        if not issubclass(model_class, SerializableModel):
            raise TypeError(
                f"{model_class.__name__} is not a subclass of SerializableModel"
            )
        self.model_class = model_class

    def dump_queryset_to_bucket(self, queryset, owner, bucket, filename):
        """
        Serialize the queryset to JSON and store it in the vault as a VaultFile with file_type='json'.
        If a file with the same name exists, bump the name (e.g., filename_1.json, filename_2.json).
        """
        base_name, ext = os.path.splitext(filename)
        title = f"{filename} (dump)"
        existing_titles = VaultFile.objects.filter(owner=owner, bucket=bucket, title__startswith=base_name).values_list(
            "title", flat=True)

        # Bump filename if needed
        bump = 1
        while title in existing_titles:
            filename = f"{base_name}_{bump}{ext}"
            title = f"{filename} (dump)"
            bump += 1

        data = self.model_class.serialize_queryset(queryset)

        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, filename)

            with open(json_path, "w") as f:
                f.write(data)

            vault_file = VaultFile.objects.create(
                owner=owner,
                title=title,
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
