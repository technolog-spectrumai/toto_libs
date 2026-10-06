"""A geometry row never outlives its link.

A link row goes with its person or community (CASCADE). Its ``Address`` or
``Zone`` would stay, a point on nobody's map, so each link's ``post_delete``
deletes what it pointed at. ``saves`` deletes the geometry row first where a
member clears one; this is for every other way a link can go.
"""

from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import (Address, CommunityHeadquarters, CommunityPin, CommunityZone,
                     PersonAddress, PinComment, Zone, ZoneComment)


@receiver(post_delete, sender=PersonAddress, dispatch_uid="geography.person_address_gone")
def _person_address_gone(sender, instance, **kwargs):
    Address.objects.filter(pk=instance.address_id).delete()


@receiver(post_delete, sender=CommunityHeadquarters, dispatch_uid="geography.headquarters_gone")
def _headquarters_gone(sender, instance, **kwargs):
    if instance.address_id:
        Address.objects.filter(pk=instance.address_id).delete()
    if instance.zone_id:
        Zone.objects.filter(pk=instance.zone_id).delete()


@receiver(post_delete, sender=CommunityPin, dispatch_uid="geography.pin_gone")
def _pin_gone(sender, instance, **kwargs):
    """A community pin went (deleted, or its community did): its point too."""
    Address.objects.filter(pk=instance.address_id).delete()


@receiver(post_delete, sender=CommunityZone, dispatch_uid="geography.community_zone_gone")
def _community_zone_gone(sender, instance, **kwargs):
    Zone.objects.filter(pk=instance.zone_id).delete()


@receiver(post_delete, sender=PinComment, dispatch_uid="geography.pin_comment_gone")
@receiver(post_delete, sender=ZoneComment, dispatch_uid="geography.zone_comment_gone")
def _comment_gone(sender, instance, **kwargs):
    """The key runs from the link to the comment, so a deleted pin or zone
    takes its links and would leave the comments, attached to nothing."""
    from toto.comments.models import Comment

    Comment.objects.filter(pk=instance.comment_id).delete()
