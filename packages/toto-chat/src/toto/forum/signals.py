"""Push membership changes to live sockets.

Membership is checked when a websocket connects, but a member can be removed long after
that — via the REST leave endpoints, the HTML leave button, or the Django admin — and
nothing about those paths closes the tab that is already connected. Without this, an
ex-member keeps receiving every message in real time until they reload.

Polling the database on every delivered message would be correct but costs a query per
message per socket. Instead, any write to ``ForumMember`` broadcasts a control frame to
the channel group; each consumer drops its cached membership answer and re-checks once,
closing the socket if the answer is now "no".
"""
import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

log = logging.getLogger(__name__)


def _notify(channel_slug):
    # channels is imported lazily: AppConfig.ready() loads this module, and importing
    # channels at module scope would make the whole app fail to load in a tier that
    # ships without it (the packaging gate's per-tier venvs, for one).
    if not channel_slug:
        return
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(
            f"forum_{channel_slug}", {"type": "membership_changed"}
        )
    except Exception:
        # Never let a presence notification break the write that triggered it.
        log.debug("forum: could not broadcast membership change", exc_info=True)


@receiver(post_save, sender="forum.ForumMember", dispatch_uid="forum_member_saved")
def member_saved(sender, instance, **kwargs):
    _notify(getattr(instance.channel, "slug", None))


@receiver(post_delete, sender="forum.ForumMember", dispatch_uid="forum_member_deleted")
def member_deleted(sender, instance, **kwargs):
    _notify(getattr(instance.channel, "slug", None))
