"""Invalidate the dynamic neomodel registry when a template changes.

Any edit/delete of a node-category or edge-type template must rebuild the
corresponding neomodel class so all workers stay consistent with the DB.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from . import registry
from .models import BentoCategory, BentoEdgeType


@receiver([post_save, post_delete], sender=BentoCategory)
@receiver([post_save, post_delete], sender=BentoEdgeType)
def _invalidate_registry(sender, instance, **kwargs):
    registry.invalidate()
