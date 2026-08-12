import shutil

from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import GitRepo


@receiver(post_delete, sender=GitRepo)
def delete_repo_dir(sender, instance, **kwargs):
    """Deleting a GitRepo removes its worktree + .git on disk. The vault files
    themselves are untouched (they live in vault storage, not the worktree)."""
    shutil.rmtree(instance.base_dir, ignore_errors=True)
