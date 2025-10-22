from django.db.models.signals import post_delete
from django.dispatch import receiver
from .models import MemoCard

@receiver(post_delete, sender=MemoCard)
def delete_card_image(sender, instance, **kwargs):
    if instance.image:
        instance.image.delete(save=False)
