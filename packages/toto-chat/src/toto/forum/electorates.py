"""The room's entry in the polls electorate registry.

Discovered by ``PollsConfig.ready()`` (the ``<app>/electorates.py`` contract —
pure data, no DB at import). On a host that ships forum without polls this
module is simply never imported, so the import below is safe by construction.

A room poll's electorate is the room: every ACTIVE member, one voice each.
Refused BY THE ENGINE, not merely by the view — the second line of defence
Business Center's electorate established, and for the same reason: if a
mis-scoped question ever reaches a voter, the answer must be no rather than
a ballot counted into the wrong room.
"""

from __future__ import annotations

from toto.polls.core import Eligibility
from toto.polls.electorates import register, register_scope_default
from toto.polls.models import SCOPE_FORUM

ROOM_MEMBERS = "room-members"


class RoomElectorate:
    """Active members of one channel, weight 1."""

    def __init__(self, channel):
        self.channel = channel

    def standing(self, question, user) -> Eligibility:
        if not self._belongs(question):
            return Eligibility(False, reason="This poll belongs to another room.")
        if not getattr(user, "is_authenticated", False):
            return Eligibility(False, reason="Sign in to vote.")

        from .permissions import member_for

        if member_for(user, self.channel) is None:
            return Eligibility(False,
                               reason="Only members of this room vote here.")
        return Eligibility(True, weight=1)

    def size(self, question) -> int:
        return self.channel.forum_members.filter(is_active=True).count()

    def _belongs(self, question) -> bool:
        return (question.scope_type == SCOPE_FORUM
                and str(question.scope_id) == str(self.channel.pk))


def _room(question):
    from .models import ForumChannel

    return RoomElectorate(ForumChannel.objects.get(pk=question.scope_id))


register(ROOM_MEMBERS, _room)
register_scope_default(SCOPE_FORUM, ROOM_MEMBERS)
