"""Communities grant; persons hold the union.

One shape of grant — a community to every member — and one resolver. Run from a
host with socialhub installed:

    python manage.py test toto.socialhub.tests_privileges

There were TWO shapes until 8/2026. ``Station`` — a "Special Role" — granted the
same four rights to whoever held the office, and carried a ``limit_multiplier``
that bought that office extra quota headroom. It fused an office, an
authorisation grant and a payslip into one row, and it went with the treasury
payroll that paid it. The tests that covered the office half went with it; what
survives here is the half that was always about membership.
"""

from django.contrib.auth.models import AnonymousUser, User
from django.test import TestCase

from toto.people.models import Person
from toto.socialhub import privileges
from toto.socialhub.models import Community, CommunityPrivilege


def make_member(name, *communities):
    user = User.objects.create_user(name, f"{name}@example.com", "pw")
    person = Person.objects.create(user=user, display_name=name)
    for community in communities:
        person.communities.add(community)
    return user


class UnionTests(TestCase):
    """Any community granting a right grants it. Highest privilege always."""

    @classmethod
    def setUpTestData(cls):
        cls.plain = Community.objects.create(name="Weavers")
        cls.agents = Community.objects.create(name="Agents")
        CommunityPrivilege.objects.create(
            community=cls.agents,
            may_see_community_chain=True,
            may_administer_communities=True,
        )

    def test_membership_is_the_grant(self):
        outsider = make_member("outsider", self.plain)
        insider = make_member("insider", self.plain, self.agents)

        self.assertFalse(privileges.has_privilege(outsider, "may_see_community_chain"))
        self.assertTrue(privileges.has_privilege(insider, "may_see_community_chain"))

    def test_expulsion_is_the_revocation(self):
        user = make_member("temp", self.agents)
        self.assertTrue(privileges.has_privilege(user, "may_administer_communities"))

        user.community_profile.communities.remove(self.agents)
        self.assertFalse(privileges.has_privilege(user, "may_administer_communities"))

    def test_belonging_to_one_granting_community_is_enough(self):
        """Highest privilege always: a plain community alongside a granting one
        does not dilute the grant."""
        user = make_member("both", self.plain, self.agents)
        self.assertTrue(privileges.has_privilege(user, "may_administer_communities"))

    def test_a_community_without_a_row_grants_nothing(self):
        user = make_member("commoner", self.plain)
        for right in privileges.RIGHTS:
            self.assertFalse(privileges.has_privilege(user, right), right)

    def test_a_mistyped_right_raises_rather_than_refusing_forever(self):
        # A typo that silently returns False is a gate nobody can pass and
        # nobody can find. Raising is the kindness here.
        user = make_member("anyone", self.plain)
        with self.assertRaises(ValueError):
            privileges.has_privilege(user, "may_fly")

    def test_anonymous_and_userless_degrade_to_the_commoner(self):
        self.assertFalse(privileges.has_privilege(AnonymousUser(),
                                                  "may_see_community_chain"))
        self.assertFalse(privileges.has_privilege(None, "may_see_community_chain"))

    def test_no_right_survives_the_office_that_used_to_grant_it(self):
        """Rights come from communities only.

        `has_privilege` had a second branch that asked Station, and a person
        holding a granting office passed without belonging to any granting
        community. Nothing should grant on this platform except membership.
        """
        user = make_member("officer", self.plain)
        for right in privileges.RIGHTS:
            self.assertFalse(privileges.has_privilege(user, right), right)

    def test_the_headroom_multiplier_is_gone(self):
        """`limit_multiplier_for` read Station.limit_multiplier and nothing
        else, so it left with it. Quota limits are flat for everybody now."""
        self.assertFalse(hasattr(privileges, "limit_multiplier_for"))
