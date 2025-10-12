from django.db.models.signals import post_delete
from django.dispatch import receiver
from .models import GitRepository

@receiver(post_delete, sender=GitRepository)
def cleanup_local_repo(sender, instance, **kwargs):
    instance.post_delete()
