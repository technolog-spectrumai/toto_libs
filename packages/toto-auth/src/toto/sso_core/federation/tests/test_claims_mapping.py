"""What a consumer host does with the claims it receives.

The end-to-end suite cannot stage an *unknown* user, because one process means
one User table (see test_federated_login). So the claim-consumption path is driven
directly here, with claims for subjects that exist on no local account — which is
the situation a real consumer host is in on its very first login.

**The class that matters most here is ``LocalAccountIsolationTests``.** Matching
used to fall back to ``username`` and then ``email__iexact``, and this file used to
assert that as intended. It is correct for a *pure* consumer, where every account
came from the provider anyway. It is an account takeover on a host that also has
local users: a studio-only account whose email happened to equal a provider
account's was adopted by that identity, kept its password so both routes reached
one account, and had its ``is_staff``/``is_superuser`` overwritten by the next
claim. A provider admin became a local admin by coincidence of email.

Matching is now only ever on a recorded ``(provider, sub)`` — see
``sso_client.models.FederatedIdentity`` — so the negative assertions below are the
real subject of this file, not an afterthought.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from toto.people.models import Person
from toto.sso_client.models import FederatedIdentity, OIDCProviderConfig
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


def provider():
    """The consumer-side provider row every identity is keyed on."""
    existing = OIDCProviderConfig.objects.filter(active=True).first()
    if existing is not None:
        return existing
    # No secret: these tests exercise claim mapping, which never reaches the
    # token exchange, so storing one through the vault would only cost an Argon2id
    # derivation per test class.
    return OIDCProviderConfig.objects.create(
        label="Portal", portal_url="http://provider.test", client_id="studio",
        active=True,
    )


def known(user, sub="subject-1", *, provisioned=False):
    """Tell this host that ``user`` answers to ``sub``. The new precondition."""
    return FederatedIdentity.objects.create(
        provider=provider(), sub=sub, user=user, provisioned=provisioned,
    )


class AutoProvisionGateTests(TestCase):
    """Whether an account unknown to this host may be created on first sign-in.

    Off by default on purpose: with it on, every account on the provider
    silently becomes an account here the moment someone follows a link. Right
    for a public consumer, wrong for an internal one.
    """

    def setUp(self):
        provider()

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

    @override_settings(TOTO_SSO_AUTO_PROVISION=True)
    def test_provisioning_records_the_identity_so_the_next_login_needs_no_gate(self):
        first = _get_or_sync_user(claims())
        identity = FederatedIdentity.objects.get(sub="subject-1")
        self.assertEqual(identity.user, first)
        self.assertTrue(identity.provisioned)

        with override_settings(TOTO_SSO_AUTO_PROVISION=False):
            self.assertEqual(_get_or_sync_user(claims()), first)

    def test_a_recorded_identity_signs_in_with_the_gate_closed(self):
        # Replaces test_a_known_account_signs_in_even_with_the_gate_closed, whose
        # notion of "known" was "shares a username or an email" — the hole. The
        # gate still governs creation and not access; "known" now means recorded.
        existing = User.objects.create_user("ada", "old@example.org", "pw")
        known(existing)
        self.assertEqual(_get_or_sync_user(claims()), existing)

    def test_the_recorded_login_time_is_updated(self):
        existing = User.objects.create_user("ada", "old@example.org", "pw")
        identity = known(existing)
        self.assertIsNone(identity.last_login_at)
        _get_or_sync_user(claims())
        identity.refresh_from_db()
        self.assertIsNotNone(identity.last_login_at)


class LocalAccountIsolationTests(TestCase):
    """A local-only account is never matched, adopted, or promoted by a claim.

    This is the security property that makes studio-only users possible. Each
    test below passed in the OPPOSITE direction before 1.23.
    """

    def setUp(self):
        provider()
        # Same email as claims() returns, and the username the provider sends as
        # preferred_username. Both of the old match paths, at once.
        self.local = User.objects.create_user(
            "remote-subject-1", "new@example.org", "localpassword",
        )

    def test_a_local_only_account_is_not_matched_by_email_or_username(self):
        self.assertIsNone(_get_or_sync_user(claims()))

    def test_a_local_only_account_keeps_its_password_and_its_fields(self):
        _get_or_sync_user(claims())
        self.local.refresh_from_db()
        self.assertTrue(self.local.has_usable_password())
        self.assertTrue(self.local.check_password("localpassword"))
        self.assertEqual(self.local.first_name, "")   # not overwritten by given_name

    def test_a_local_only_account_is_not_handed_privileges_by_a_claim(self):
        # The escalation: a provider admin whose email matched made the local
        # account a superuser here.
        _get_or_sync_user(claims(roles=["admin", "staff"]))
        self.local.refresh_from_db()
        self.assertFalse(self.local.is_superuser)
        self.assertFalse(self.local.is_staff)

    def test_a_local_only_account_cannot_be_deactivated_by_a_claim(self):
        _get_or_sync_user(claims(is_active=False))
        self.local.refresh_from_db()
        self.assertTrue(self.local.is_active)

    @override_settings(TOTO_SSO_AUTO_PROVISION=True)
    def test_a_colliding_email_provisions_a_SEPARATE_account(self):
        user = _get_or_sync_user(claims())
        self.assertNotEqual(user.pk, self.local.pk)
        self.assertEqual(user.username, "oidc_subject-1")
        self.assertEqual(User.objects.filter(email__iexact="new@example.org").count(), 2)
        self.local.refresh_from_db()
        self.assertTrue(self.local.check_password("localpassword"))

    def test_two_subjects_cannot_share_one_recorded_identity(self):
        from django.db import IntegrityError, transaction

        known(self.local)
        other = User.objects.create_user("bob", "bob@example.org", "pw")
        with self.assertRaises(IntegrityError), transaction.atomic():
            known(other)   # same (provider, sub)

    def test_one_account_may_answer_to_several_subjects(self):
        # The constraint is on (provider, sub), not on user: an account can be
        # reachable through more than one identity without either being ambiguous.
        known(self.local, "subject-1")
        known(self.local, "subject-2")
        self.assertEqual(self.local.federated_identities.count(), 2)
        self.assertEqual(_get_or_sync_user(claims("subject-2")), self.local)


class RoleMappingTests(TestCase):
    """Privileges follow the provider, on every sign-in — for federated accounts.

    Without this a federated host has no administrators at all: the consumer
    never asked for the roles scope and never read it. What changed in 1.23 is
    only *which* accounts this applies to: ones with a recorded identity.
    """

    def setUp(self):
        provider()
        self.user = User.objects.create_user("remote-subject-1", "u@example.org", "pw")
        known(self.user)

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
        provider()
        self.user = User.objects.create_user("remote-subject-1", "u@example.org", "pw")
        known(self.user)

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


class NoProviderConfiguredTests(TestCase):
    """With no active provider row there is nothing to key an identity on.

    It must fail closed rather than fall back to matching something by
    coincidence, which is what the removed email path would have done.
    """

    def test_claims_match_nothing_when_no_provider_is_configured(self):
        User.objects.create_user("remote-subject-1", "new@example.org", "pw")
        self.assertIsNone(_get_or_sync_user(claims()))


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
