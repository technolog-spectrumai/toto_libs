import os
from django.db.models.signals import post_delete
from django.dispatch import receiver
from toto.vault.models import VaultFile


@receiver(post_delete, sender=VaultFile)
def delete_file_on_disk(sender, instance, **kwargs):
    """Deletes the physical file when a VaultFile record is removed."""
    if instance.file and os.path.isfile(instance.file.path):
        try:
            os.remove(instance.file.path)
        except Exception as e:
            print(f"Error deleting file {instance.file.path}: {e}")
