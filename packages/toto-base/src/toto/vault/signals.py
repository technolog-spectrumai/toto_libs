import os
from django.db.models.signals import post_delete
from django.dispatch import Signal, receiver
from toto.vault.models import VaultFile

#: Sent once a file has actually been encrypted (``VaultFile.encrypt``, the one
#: method all four encrypt doors call). Keyword argument: ``file``.
#:
#: A signal rather than a call because the listeners live in wheels toto-base
#: may not import — ``toto.mana`` rewards the act in security mana — and a
#: signal is an edge that points the allowed way. Sent with ``send_robust``: a
#: finished encryption must never turn into an error because a listener failed.
file_encrypted = Signal()


@receiver(post_delete, sender=VaultFile)
def delete_file_on_disk(sender, instance, **kwargs):
    """Deletes the physical file when a VaultFile record is removed."""
    if instance.file and os.path.isfile(instance.file.path):
        try:
            os.remove(instance.file.path)
        except Exception as e:
            print(f"Error deleting file {instance.file.path}: {e}")
