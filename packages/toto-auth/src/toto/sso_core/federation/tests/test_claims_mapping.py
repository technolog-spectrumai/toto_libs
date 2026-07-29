"""What a consumer host does with the claims it receives.

The end-to-end suite cannot stage an *unknown* user, because one process means
one User table and the provider's own row always matches (see
test_federated_login). So the claim-consumption path is driven directly here,
with claims for subjects that exist on no local account — which is the situation
a real consumer host is in on its very first login.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from toto.people.models import Person
from toto.sso_client.views import _get_or_sync_user, _link_person, auto_provision_enabled

User = get_user_model()


def claims(sub="subject-1", **extra):
    base = {
        "sub": sub,
        "email": "new@example.org",
        "given_name": "New",
        "family_name": "Person",
        "preferred_username": f"remote-{sub}",
    }
    base.update(extra)
    return base


class AutoProvisionGateTests(TestCase):
    """Whether an account unknown to this host may be created on first sign-in.

    Off by default on purpose: with it on, every account on the provider
    silently becomes an account here the moment someone follows a link. Right
    for a public consumer, wrong for an internal one.
    """

    def test_the_gate_is_closed_unless_the_host_opens_it(self):
        self.assertFalse(auto_provision_enabled())

    def test_an_unknown_subject_is_refused_while_the_gate_is_closed(self):
        self.assertIsNone(_get_or_sync_user(claims()))
        self.assertFalse(User.objects.filter(username="oidc_subject-1").exists())

    @override_settings(TOTO_SSO_AUTO_PROVISION=True)
    def test_an_unknown_subject_is_provisioned_once_the_gate_is_open(self):
        user = _get_or_sync_user(claims())
        self.assertEqual(user.username, "oidc_subject-1")
        self.assertEqual(user.email, "new@example.org")
        self.assertEqual(user.first_name, "New")
        # No local password: this account can only ever be reached through the
        # provider.
        self.assertFalse(user.has_usable_password())

    def test_a_known_account_signs_in_even_with_the_gate_closed(self):
        # The gate governs creation, not access — otherwise closing it would
        # lock out the people who already have accounts.
        existing = User.objects.create_user("remote-subject-1", "old@example.org", "pw")
        self.assertEqual(_get_or_sync_user(claims()), existing)


class RoleMappingTests(TestCase):
    """Privileges follow the provider, on every sign-in.

    Without this a federated host has no administrators at all: the consumer
    never asked for the roles scope and never read it.
    """

    def setUp(self):
        self.user = User.objects.create_user("remote-subject-1", "u@example.org", "pw")

    def test_admin_role_grants_superuser_and_staff(self):
        _get_or_sync_user(claims(roles=["admin", "staff"]))
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_superuser)
        self.assertTrue(self.user.is_staff)

    def test_staff_role_grants_staff_only(self):
        _get_or_sync_user(claims(roles=["staff"]))
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_superuser)
        self.assertTrue(self.user.is_staff)

    def test_viewer_role_grants_nothing(self):
        _get_or_sync_user(claims(roles=["viewer"]))
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_superuser)
        self.assertFalse(self.user.is_staff)

    def test_revoking_a_role_upstream_revokes_it_here_on_next_login(self):
        # The reason claims are applied on every sign-in and not just at
        # creation: a stale local grant would outlive the upstream one.
        self.user.is_superuser = self.user.is_staff = True
        self.user.save()
        _get_or_sync_user(claims(roles=["viewer"]))
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_superuser)
        self.assertFalse(self.user.is_staff)

    def test_a_provider_that_grants_no_roles_scope_leaves_local_flags_alone(self):
        # Absent claim means "not told", not "told no" — clearing flags here
        # would silently demote every admin on a portal without the scope.
        self.user.is_staff = True
        self.user.save()
        _get_or_sync_user(claims())
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_staff)


class ActiveFlagTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("remote-subject-1", "u@example.org", "pw")

    def test_deactivation_propagates(self):
        _get_or_sync_user(claims(is_active=False))
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_reactivation_propagates(self):
        self.user.is_active = False
        self.user.save()
        _get_or_sync_user(claims(is_active=True))
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)


class PersonProvisioningTests(TestCase):
    """A community profile for the federated user.

    This is not cosmetic. Several apps a consumer host is likely to install have
    NOT NULL foreign keys to Person — kanban.Project.project_lead,
    kanban.Practitioner.person, forum.ForumMember.person — and a consumer host
    starts with an empty people table. Without provisioning, the host comes up
    unable to create a project or join a channel.
    """

    def setUp(self):
        self.user = User.objects.create_user("ada", "ada@example.org", "pw",
                                             first_name="Ada", last_name="Lovelace")

    def test_a_person_is_created_when_none_matches(self):
        person = _link_person(self.user, None, "subject-1")
        self.assertIsNotNone(person)
        self.assertEqual(person.user, self.user)
        self.assertEqual(person.federated_sub, "subject-1")
        self.assertEqual(person.display_name, "Ada Lovelace")

    def test_an_existing_unclaimed_person_is_adopted_by_slug(self):
        # A locally seeded record the provider turns out to know about.
        seeded = Person.objects.create(display_name="Ada Seeded", slug="ada-seeded")
        person = _link_person(self.user, "ada-seeded", "subject-1")
        self.assertEqual(person.pk, seeded.pk)
        self.assertEqual(person.user, self.user)
        self.assertEqual(person.federated_sub, "subject-1")

    def test_the_same_subject_returns_the_same_person(self):
        first = _link_person(self.user, None, "subject-1")
        second = _link_person(self.user, None, "subject-1")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Person.objects.filter(federated_sub="subject-1").count(), 1)

    def test_a_person_is_not_duplicated_for_a_user_that_already_has_one(self):
        Person.objects.create(user=self.user, display_name="Ada", slug="ada")
        self.assertIsNone(_link_person(self.user, None, "subject-1"))
        self.assertEqual(Person.objects.filter(user=self.user).count(), 1)

    def test_a_claimed_person_is_not_stolen_by_slug(self):
        # Adoption only ever takes an UNclaimed record; otherwise one user's
        # sign-in could walk off with another's profile.
        other = User.objects.create_user("bob", "bob@example.org", "pw")
        Person.objects.create(user=other, display_name="Bob", slug="bob")
        person = _link_person(self.user, "bob", "subject-1")
        self.assertNotEqual(person.user, other)
        self.assertEqual(Person.objects.get(slug="bob").user, other)
