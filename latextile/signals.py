from django.db.models.signals import pre_delete
from django.dispatch import receiver
from .models import LatexProject

@receiver(pre_delete, sender=LatexProject)
def delete_project_directory(sender, instance, **kwargs):
    instance.clean_directory()
