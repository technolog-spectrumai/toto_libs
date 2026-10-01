"""Membership applications that lapse (2026-10-01, RODO; ``applications.py``).

Applying again renews a lapsed application instead of refusing its address
for ever, and the nightly housekeeping prunes the ones that lapsed long ago
with the never-used accounts they made — never an account that got in or
holds anything (an application whose user has data is kept).

    manage.py test toto.socialhub.tests_application_housekeeping
"""

from datetime import timedelta
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub import applications
from toto.socialhub.models import (Community, DataExport, ErasureRequest, MembershipApplication,
                                   PrivacyAcceptance, PrivacyNotice, ReferenceRequest)

User = get_user_model()
EMAIL = "newbie@example.com"


class LapsedCase(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        self.guild = Community.objects.create(name="Cedar Guild", slug="cedar")
        self.other = Community.objects.create(name="Pine Guild", slug="pine")
        PrivacyNotice.objects.create(version=1, text_pl="Informacja", text_en="Notice")
        # What the application view leaves behind: an inactive account and
        # the application, whose week ran out yesterday.
        self.applicant = User.objects.create(username="newbie", email=EMAIL, is_active=False)
        self.application = MembershipApplication.objects.create(
            email=EMAIL, community=self.guild, code="424242",
            expires_at=timezone.now() - timedelta(days=1), privacy_version=1,
            privacy_accepted_at=timezone.now() - timedelta(days=8))

    def lapse(self, days, application=None):
        MembershipApplication.objects.filter(pk=(application or self.application).pk).update(
            expires_at=timezone.now() - timedelta(days=days))

    def apply(self, username="newbie", email=EMAIL, version=1):
        return self.client.post(reverse("socialhub:membership_application"), {
            "username": username, "email": email, "community": self.other.pk,
            "privacy_version": version, "privacy_accept": "on"})

    def admit_and_move(self):
        """Admitted through the application, then moved to another address
        on My account: no account has the application's address any more."""
        voucher = Person.objects.create(
            user=User.objects.create_user("voucher", "v@example.com", "pw"), display_name="V")
        ReferenceRequest.objects.create(application=self.application, referrer=voucher,
                                        status="accepted")
        User.objects.filter(pk=self.applicant.pk).update(is_active=True,
                                                         email="moved@example.com")

    def refused(self, response, field="email"):
        self.assertEqual(response.status_code, 200)
        self.assertIn(field, response.context["form"].errors)
        self.application.refresh_from_db()
        self.assertEqual((self.application.code, self.application.community),
                         ("424242", self.guild))


class RenewalTests(LapsedCase):
    def test_applying_again_renews_the_lapsed_application(self):
        response = self.apply()
        self.assertRedirects(response, reverse("socialhub:application_success", args=[EMAIL]),
                             fetch_redirect_response=False)
        self.assertEqual(MembershipApplication.objects.count(), 1)
        self.application.refresh_from_db()
        self.assertRegex(self.application.code, r"^\d{6}$")
        self.assertNotEqual(self.application.code, "424242")
        self.assertAlmostEqual((self.application.expires_at - timezone.now()).total_seconds(),
                               timedelta(days=7).total_seconds(), delta=60)
        self.assertFalse(self.application.is_expired())
        self.assertEqual((self.application.status, self.application.verified_at,
                          self.application.community), ("pending", None, self.other))
        # The account the lapsed attempt made, still waiting for a reference.
        self.assertEqual(list(User.objects.filter(email__iexact=EMAIL)), [self.applicant])
        self.applicant.refresh_from_db()
        self.assertFalse(self.applicant.is_active)

    def test_the_new_code_verifies(self):
        self.apply()
        self.application.refresh_from_db()
        response = self.client.post(reverse("socialhub:membership_verification", args=[EMAIL]),
                                    {"code": self.application.code})
        self.assertRedirects(response, reverse("socialhub:reference_request",
                                               args=[self.application.pk]),
                             fetch_redirect_response=False)

    def test_the_lapsed_code_says_how_to_get_a_new_one(self):
        response = self.client.post(reverse("socialhub:membership_verification", args=[EMAIL]),
                                    {"code": "424242"})
        self.assertIn("Apply again with the same e-mail address", response.context["error"])
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, "pending")

    def test_a_verified_attempt_starts_over_without_its_references(self):
        voucher = Person.objects.create(
            user=User.objects.create_user("voucher", "v@example.com", "pw"), display_name="V")
        voucher.communities.add(self.guild)
        MembershipApplication.objects.filter(pk=self.application.pk).update(
            status="verified", verified_at=timezone.now() - timedelta(days=3))
        ReferenceRequest.objects.create(application=self.application, referrer=voucher)
        ReferenceRequest.objects.create(application=self.application, referrer=voucher,
                                        status="declined")
        self.apply()
        self.application.refresh_from_db()
        self.assertEqual((self.application.status, self.application.verified_at),
                         ("pending", None))
        # A pending one would admit them to the community they chose THIS time.
        self.assertFalse(ReferenceRequest.objects.filter(application=self.application).exists())

    def test_a_new_username_renames_the_account_it_made(self):
        self.apply(username="newbie2")
        self.applicant.refresh_from_db()
        self.assertEqual(self.applicant.username, "newbie2")
        self.assertEqual(User.objects.filter(email__iexact=EMAIL).count(), 1)

    def test_with_no_account_left_one_is_made(self):
        self.applicant.delete()
        self.apply(username="again")
        account = User.objects.get(username="again")
        self.assertEqual((account.email, account.is_active), (EMAIL, False))

    def test_somebody_elses_username_is_still_refused(self):
        User.objects.create_user("taken", "taken@example.com", "pw")
        self.refused(self.apply(username="taken"), field="username")

    def test_the_notice_accepted_now_is_recorded_and_the_chain_says_renewed(self):
        PrivacyNotice.objects.create(version=2, text_pl="Informacja 2", text_en="Notice 2")
        self.apply(version=2)
        self.application.refresh_from_db()
        self.assertEqual(self.application.privacy_version, 2)
        self.assertAlmostEqual((timezone.now() - self.application.privacy_accepted_at)
                               .total_seconds(), 0, delta=60)
        if not apps.is_installed("toto.audit"):
            return
        from toto.audit.models import AuditRecord

        mine = AuditRecord.objects.filter(object_type="socialhub.membershipapplication",
                                          object_id=str(self.application.pk))
        renewed = mine.get(action="SOCIALHUB.APPLICATION_RENEWED")
        self.assertEqual(renewed.metadata["community"], "pine")
        self.assertFalse(mine.filter(action="SOCIALHUB.APPLICATION_PENDING").exists())
        self.assertEqual(mine.get(action="PRIVACY.NOTICE_ACCEPTED").metadata["version"], 2)

    def test_a_live_application_still_holds_its_address(self):
        MembershipApplication.objects.filter(pk=self.application.pk).update(
            expires_at=timezone.now() + timedelta(days=3))
        self.refused(self.apply())

    def test_an_address_whose_account_got_in_is_not_renewed(self):
        User.objects.filter(pk=self.applicant.pk).update(is_active=True)
        self.refused(self.apply(username="newbie2"))

    def test_a_members_old_address_does_not_renew_their_application(self):
        # (2026-10-01) It read as a leftover once the member's account moved
        # to another address: anybody typing the old one reset it.
        self.admit_and_move()
        self.refused(self.apply(username="someone"))
        self.assertTrue(ReferenceRequest.objects.filter(application=self.application,
                                                        status="accepted").exists())

    def test_an_account_that_holds_something_is_not_renewed(self):
        Person.objects.create(user=self.applicant, display_name="Newbie")
        self.refused(self.apply(username="newbie2"))
        self.applicant.refresh_from_db()
        self.assertEqual(self.applicant.username, "newbie")


class PruneTests(LapsedCase):
    def setUp(self):
        super().setUp()
        self.lapse(days=31)

    def gone(self):
        return (not MembershipApplication.objects.filter(pk=self.application.pk).exists(),
                not User.objects.filter(pk=self.applicant.pk).exists())

    def test_a_long_lapsed_application_goes_with_the_account_it_made(self):
        self.assertEqual(applications.prune(), {"pruned": 1, "accounts_deleted": 1, "kept": 0})
        self.assertEqual(self.gone(), (True, True))

    def test_what_sign_up_gave_the_account_does_not_keep_it(self):
        # The prepaid ledger account every new account gets (toto.assets)
        # stays, detached — erase_user keeps it the same way.
        if not apps.is_installed("toto.assets"):
            self.skipTest("toto.assets is not installed")
        from toto.assets.models import LedgerAccount

        ledger = LedgerAccount.objects.get(user=self.applicant)
        self.assertEqual(applications.prune()["pruned"], 1)
        ledger.refresh_from_db()
        self.assertIsNone(ledger.user_id)

    def test_a_recently_lapsed_or_live_application_stays(self):
        self.lapse(days=29)
        live = MembershipApplication.objects.create(
            email="live@example.com", community=self.guild, code="515151",
            expires_at=timezone.now() + timedelta(days=3))
        self.assertEqual(applications.prune(), {"pruned": 0, "accounts_deleted": 0, "kept": 0})
        self.assertEqual(self.gone(), (False, False))
        self.assertTrue(MembershipApplication.objects.filter(pk=live.pk).exists())

    @override_settings(SOCIALHUB_EXPIRED_APPLICATION_DAYS=5)
    def test_the_setting_moves_the_cut(self):
        self.lapse(days=6)
        self.assertEqual(applications.prune()["pruned"], 1)

    def test_a_members_application_is_theirs_not_housekeepings(self):
        User.objects.filter(pk=self.applicant.pk).update(is_active=True)
        self.assertEqual(applications.prune(), {"pruned": 0, "accounts_deleted": 0, "kept": 0})
        self.assertEqual(self.gone(), (False, False))

    def test_a_member_who_changed_their_address_keeps_their_application(self):
        # (2026-10-01) The accepted reference says who got in through it,
        # whatever address their account has now.
        self.admit_and_move()
        self.assertEqual(applications.prune(), {"pruned": 0, "accounts_deleted": 0, "kept": 0})
        self.assertTrue(MembershipApplication.objects.filter(pk=self.application.pk).exists())
        self.assertTrue(ReferenceRequest.objects.filter(application=self.application).exists())

    def test_an_account_that_signed_in_or_is_staff_is_somebodys(self):
        for flags in ({"last_login": timezone.now()}, {"is_staff": True}, {"is_superuser": True}):
            with self.subTest(**{k: str(v) for k, v in flags.items()}):
                User.objects.filter(pk=self.applicant.pk).update(**flags)
                self.assertEqual(applications.prune()["pruned"], 0)
                self.assertEqual(self.gone(), (False, False))
                User.objects.filter(pk=self.applicant.pk).update(
                    last_login=None, is_staff=False, is_superuser=False)

    def assert_kept(self):
        self.assertEqual(applications.prune(), {"pruned": 0, "accounts_deleted": 0, "kept": 1})
        self.assertEqual(self.gone(), (False, False))

    def test_an_application_whose_user_has_a_person_is_kept(self):
        Person.objects.create(user=self.applicant, display_name="Newbie")
        self.assert_kept()

    def test_a_privacy_acceptance_keeps_it(self):
        person = Person.objects.create(user=self.applicant, display_name="Newbie")
        PrivacyAcceptance.objects.create(person=person, version=1, accepted_at=timezone.now())
        self.assert_kept()

    def test_a_data_export_keeps_it(self):
        DataExport.objects.create(user=self.applicant)
        self.assert_kept()

    def test_an_open_erasure_request_keeps_it(self):
        # SET_NULL: the walk would only detach it, and that is enough to keep.
        ErasureRequest.objects.create(user=self.applicant, username="newbie")
        self.assert_kept()

    def test_every_account_with_the_address_must_be_a_leftover(self):
        older = User.objects.create(username="newbie-old", email=EMAIL.upper(), is_active=False)
        Person.objects.create(user=older, display_name="Old")
        self.assert_kept()
        self.assertTrue(User.objects.filter(pk=older.pk).exists())

    def test_an_application_whose_account_is_gone_goes_alone(self):
        self.applicant.delete()
        self.assertEqual(applications.prune(), {"pruned": 1, "accounts_deleted": 0, "kept": 0})

    def test_a_failure_keeps_it_for_the_next_night(self):
        with mock.patch.object(MembershipApplication, "delete", side_effect=RuntimeError("disk")):
            self.assertEqual(applications.prune(), {"pruned": 0, "accounts_deleted": 0, "kept": 1})
        # Rolled back together: the account did not go without its application.
        self.assertEqual(self.gone(), (False, False))


class OwnsNothingElseTests(TestCase):
    """The rule over erase_user's report, whatever the host has installed."""

    def setUp(self):
        self.account = User.objects.create(username="x", email="x@example.com", is_active=False)

    def verdict(self, deleted=None, detached=None, blocked_by=()):
        report = {"deleted": {"auth.User": 1, "core.UserSession": 0, **(deleted or {})},
                  "detached": {"vault.Bucket.owner": 0, **(detached or {})},
                  "blocked_by": list(blocked_by)}
        with mock.patch("toto.core.management.commands.erase_user.plan", return_value=report):
            return applications.owns_nothing_else(self.account)

    def test_the_account_alone_and_tables_at_zero_own_nothing(self):
        self.assertTrue(self.verdict())

    def test_what_sign_up_makes_is_not_something_else(self):
        self.assertTrue(self.verdict(
            deleted={"mana.ManaGrant": 3, "assets.FaucetPayout": 3},
            detached={"assets.LedgerAccount.user": 1, "mana.ManaGrant.payout": 3}))

    def test_anything_else_deleted_detached_or_blocking_is_owned(self):
        self.assertFalse(self.verdict(deleted={"people.Person": 1}))
        self.assertFalse(self.verdict(detached={"vault.Bucket.owner": 1}))
        self.assertFalse(self.verdict(blocked_by=["comments.Comment"]))
