# vault/signals.py

import os
from django.db.models.signals import post_delete
from django.dispatch import receiver
from toto.vault.models import VaultFile

@receiver(post_delete, sender=VaultFile)
def delete_file_on_disk(sender, instance, **kwargs):
    """
    Deletes the file from the filesystem when the VaultFile object is deleted.
    """
    if instance.file and os.path.isfile(instance.file.path):
        try:
            os.remove(instance.file.path)
        except Exception as e:
            # Optional: log the error or handle it gracefully
            print(f"Error deleting file {instance.file.path}: {e}")
