"""Who answers a poll posted in a room: the room.

A poll in a forum room is answered by that room's active members, one voice
each — the same rule that decides who reads the room's messages, applied to
its polls. It is a **visibility** rule. It confers no authority, nobody can
configure it, and a poll cannot be pointed at a different room than the one it
was posted in.

Registered from this app's ``ready()`` rather than discovered, so a host that
ships forum without polls never reaches the import, and a host that ships
polls without forum simply has no room audience.
"""

from __future__ import annotations

from toto.polls.core import Eligibility
from toto.polls.models import SCOPE_FORUM


class RoomAudience:
    """Active members of one channel, weight 1."""

    def __init__(self, channel):
        self.channel = channel

    def standing(self, question, user) -> Eligibility:
        if not self._belongs(question):
            return Eligibility(False, reason="This poll belongs to another room.")
        if not getattr(user, "is_authenticated", False):
            return Eligibility(False, reason="Sign in to answer.")

        from .permissions import member_for

        if member_for(user, self.channel) is None:
            return Eligibility(False,
                               reason="Only members of this room answer here.")
        return Eligibility(True, weight=1)

    def size(self, question) -> int:
        return self.channel.forum_members.filter(is_active=True).count()

    def _belongs(self, question) -> bool:
        return (question.scope_type == SCOPE_FORUM
                and str(question.scope_id) == str(self.channel.pk))


def room_audience(question) -> RoomAudience:
    from .models import ForumChannel

    return RoomAudience(ForumChannel.objects.get(pk=question.scope_id))


def install() -> None:
    """Tell polls who answers a room's polls. Called from ``ready()``."""
    from toto.polls.services import register_audience

    register_audience(SCOPE_FORUM, room_audience)
