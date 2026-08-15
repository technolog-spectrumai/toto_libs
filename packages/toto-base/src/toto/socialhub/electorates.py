"""The community's entry in the polls electorate registry.

Discovered by ``PollsConfig.ready()`` (the ``<app>/electorates.py`` contract —
pure data, no DB at import). socialhub ships to three hosts and polls only to
one, so the import below has to be safe by construction: on a host without
polls this module is simply never imported.

A community vote's electorate is the community: every member, one voice each.
Refused BY THE ENGINE, not merely by a view — if a mis-scoped question ever
reaches somebody, the answer must be no rather than a ballot counted into the
wrong community.

**This is where company votes live now.** A company is a community whose
``org_type`` is "company" (socialhub has had that choice since 0001), so the
Business Center's assembly and board electorates retired with it: one register
— membership — and weights that an operator configures as data when a
community wants them, exactly as Stage 8 of the governance campaign decreed
("do not derive voting rights or weights automatically from shares").

A configured :class:`~toto.polls.electorate_models.Electorate` beats this
registry entry whenever one exists, and a frozen roll beats both — see
``polls.electorates.resolve``.
"""

from __future__ import annotations

from toto.polls.core import Eligibility
from toto.polls.electorates import register, register_scope_default
from toto.polls.models import SCOPE_COMMUNITY

COMMUNITY_MEMBERS = "community-members"


class CommunityElectorate:
    """Members of one community, weight 1."""

    def __init__(self, community):
        self.community = community

    def standing(self, question, user) -> Eligibility:
        if not self._belongs(question):
            return Eligibility(
                False, reason="This vote belongs to another community.")
        if not getattr(user, "is_authenticated", False):
            return Eligibility(False, reason="Sign in to vote.")

        # Membership hangs off people.Person, and Person.user is nullable —
        # a member without a login is on the register and counts toward its
        # size, but cannot cast. The same split the company registers had.
        person = getattr(user, "community_profile", None)
        if person is None or not person.communities.filter(
                pk=self.community.pk).exists():
            return Eligibility(
                False, reason="Only members of this community vote here.")
        return Eligibility(True, weight=1)

    def size(self, question) -> int:
        return self.community.members.count()

    def _belongs(self, question) -> bool:
        return (question.scope_type == SCOPE_COMMUNITY
                and str(question.scope_id) == str(self.community.pk))


def _community(question):
    from .models import Community

    return CommunityElectorate(Community.objects.get(pk=question.scope_id))


register(COMMUNITY_MEMBERS, _community)
register_scope_default(SCOPE_COMMUNITY, COMMUNITY_MEMBERS)
