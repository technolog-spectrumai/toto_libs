# vault/signals.py
import os
from django.db.models.signals import post_delete, post_save
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


@receiver(post_save, sender=VaultFile, dispatch_uid="vault_charge_upload")
def charge_upload_on_create(sender, instance, created, **kwargs):
    """Post storage.request + storage.transfer_mb charges when a new file is saved."""
    if not created:
        return
    from toto.vault.billing import charge_upload
    # ValueError = insufficient funds (should have been caught by preflight_upload_check
    # in the view; reaching here means a race condition — let it propagate so the
    # caller knows the file was saved but not billed).
    # Other unexpected errors are logged but not re-raised to avoid orphaned files.
    try:
        charge_upload(instance)
    except ValueError:
        raise
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error(
            "vault billing: unexpected error charging upload for file %s: %s",
            instance.pk, exc,
        )
