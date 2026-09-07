"""The two-flow password reset: flow selection, and flow 2 end to end.

Runs under a full host's settings (the gate names this module), so people,
socialhub, jess and audit are all real. The email flow's classic branch is
covered by ``sso_master.tests.test_password_reset``; here the concerns are the
NEW surfaces: which flow a request gets, the inline send, and the whole life
of a recovery ticket — filed, approved, redeemed, burned, expired — with the
anti-enumeration and never-sees-the-password properties asserted outright.
"""
import hashlib
import uuid
from unittest.mock import patch

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform

from . import recovery
from .models import RecoveryTicket

User = get_user_model()

CONSOLE = "django.core.mail.backends.console.EmailBackend"
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"


def _platform():
    Platform.objects.create(site_name="Toto", author="T", publication_year=2026)


def _person(user, display_name, **kwargs):
    from toto.people.models import Person

    return Person.objects.create(user=user, display_name=display_name, **kwargs)


class RecoveryBase(TestCase):
    def setUp(self):
        _platform()
        self.client = Client()
        self.patron_user = User.objects.create_user(
            "patron", email="patron@x.test", password="pw",
        )
        self.patron = _person(self.patron_user, "Patron")
        self.user = User.objects.create_user(
            "mentee", email="mentee@x.test", password="old-pw",
        )
        self.person = _person(self.user, "Mentee", patron=self.patron)

    def _file(self):
        return recovery.file_request(self.user.username)


@override_settings(EMAIL_BACKEND=CONSOLE, RESET_REQUEST_COOLDOWN_SECONDS=0)
class TicketRequestTests(RecoveryBase):
    URL = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.URL = reverse("sso:password_reset")

    def test_the_page_serves_the_username_form(self):
        response = self.client.get(self.URL)
        self.assertContains(response, "Request Recovery")
        self.assertNotContains(response, "Send Reset Link")

    def test_a_request_files_one_ticket_for_the_patron(self):
        response = self.client.post(self.URL, {"username": "mentee"})
        self.assertRedirects(
            response, reverse("sso:password_reset_done") + "?flow=ticket"
        )
        ticket = RecoveryTicket.objects.get()
        self.assertEqual(ticket.user, self.user)
        self.assertEqual(ticket.approver, self.patron_user)
        self.assertEqual(ticket.approver_rule, RecoveryTicket.RULE_PATRON)
        self.assertEqual(ticket.status, RecoveryTicket.PENDING)
        self.assertGreater(ticket.request_expires_at, timezone.now())

    def test_an_unknown_username_gets_the_same_response_and_no_ticket(self):
        known = self.client.post(self.URL, {"username": "mentee"})
        unknown = self.client.post(self.URL, {"username": "nobody-here"})
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.url, unknown.url)
        self.assertEqual(RecoveryTicket.objects.count(), 1)

    def test_ineligible_accounts_file_nothing(self):
        # Inactive, and federated (unusable password) — the same exclusions
        # Django's own PasswordResetForm.get_users applies to the email flow.
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.client.post(self.URL, {"username": "mentee"})

        federated = User.objects.create_user("oidc_sub-9", email="f@x.test")
        federated.set_unusable_password()
        federated.save(update_fields=["password"])
        self.client.post(self.URL, {"username": "oidc_sub-9"})

        self.assertEqual(RecoveryTicket.objects.count(), 0)

    def test_a_pending_ticket_dedupes_the_next_request(self):
        self.client.post(self.URL, {"username": "mentee"})
        self.client.post(self.URL, {"username": "mentee"})
        self.assertEqual(RecoveryTicket.objects.count(), 1)

    @override_settings(RESET_REQUEST_COOLDOWN_SECONDS=60)
    def test_the_cooldown_holds_the_second_request(self):
        # Two different usernames, so what blocks the second is the cooldown
        # alone — not the one-pending-per-user constraint.
        other = User.objects.create_user("other", email="o@x.test", password="pw")
        self.client.post(self.URL, {"username": "mentee"})
        response = self.client.post(self.URL, {"username": "other"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "wait")
        self.assertEqual(RecoveryTicket.objects.count(), 1)

    def test_the_request_is_audited(self):
        if not django_apps.is_installed("toto.audit"):
            self.skipTest("audit not installed on this host")
        from toto.audit.models import AuditRecord

        self.client.post(self.URL, {"username": "mentee"})
        record = AuditRecord.objects.get(action="PASSWORD_RECOVERY_REQUESTED")
        self.assertIn("mentee", record.object_description)


@override_settings(EMAIL_BACKEND=CONSOLE, RESET_REQUEST_COOLDOWN_SECONDS=0)
class FlowFlipTests(RecoveryBase):
    """The client's form and the server's flow can disagree — a lock, an
    expiry or a worker restart between page load and POST. Faking success
    would be a lie twice over: no ticket exists and no mail was sent."""

    def test_an_email_post_into_ticket_mode_is_told_the_truth(self):
        response = self.client.post(
            reverse("sso:password_reset"), {"email": self.user.email},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "can no longer send reset emails")
        self.assertContains(response, "Request Recovery")
        self.assertEqual(RecoveryTicket.objects.count(), 0)

    def test_a_vanished_inline_credential_leaves_a_failed_outbox_row(self):
        if not django_apps.is_installed("toto.jess"):
            self.skipTest("jess not installed on this host")
        from toto.jess.models import MailMessage

        with patch("toto.sso_core.password_reset._email_send_mode",
                   return_value="inline"), \
             patch("toto.jess.credentials.credential", return_value=None):
            response = self.client.post(
                reverse("sso:password_reset"), {"email": self.user.email},
            )
        self.assertRedirects(response, reverse("sso:password_reset_done"))
        row = MailMessage.objects.get()
        self.assertEqual(row.status, MailMessage.FAILED)
        self.assertIn("credential", row.error)


class ApproverResolutionTests(RecoveryBase):
    def test_the_patron_wins(self):
        approver, rule = recovery.resolve_approver(self.user)
        self.assertEqual(approver, self.patron_user)
        self.assertEqual(rule, RecoveryTicket.RULE_PATRON)

    def test_a_patron_without_a_login_falls_through(self):
        from toto.people.models import Person

        ghost = Person.objects.create(display_name="Ghost")
        self.person.patron = ghost
        self.person.save(update_fields=["patron"])
        approver, rule = recovery.resolve_approver(self.user)
        self.assertIsNone(approver)
        self.assertEqual(rule, RecoveryTicket.RULE_STAFF)

    def test_the_accepted_referrer_is_second(self):
        from toto.socialhub.models import (
            Community, MembershipApplication, ReferenceRequest,
        )

        self.person.patron = None
        self.person.save(update_fields=["patron"])
        community = Community.objects.create(name="Cedar Guild")
        application = MembershipApplication.objects.create(
            email=self.user.email, community=community, code="111222",
            verified_at=timezone.now(), status="verified",
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )
        ReferenceRequest.objects.create(
            application=application, referrer=self.patron,
            status="accepted", responded_at=timezone.now(),
        )
        approver, rule = recovery.resolve_approver(self.user)
        self.assertEqual(approver, self.patron_user)
        self.assertEqual(rule, RecoveryTicket.RULE_REFERRER)

    def test_a_dated_acceptance_outranks_an_undated_one(self):
        # Postgres puts NULLs first under a plain DESC; nulls_last is what
        # keeps an admin-created acceptance with no responded_at from
        # outranking every real one.
        from toto.socialhub.models import (
            Community, MembershipApplication, ReferenceRequest,
        )

        self.person.patron = None
        self.person.save(update_fields=["patron"])
        community = Community.objects.create(name="Cedar Guild")
        application = MembershipApplication.objects.create(
            email=self.user.email, community=community, code="111222",
            verified_at=timezone.now(), status="verified",
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )
        ghost_user = User.objects.create_user("ghostref", password="pw")
        ghost = _person(ghost_user, "Ghost Ref")
        ReferenceRequest.objects.create(
            application=application, referrer=ghost, status="accepted",
        )
        ReferenceRequest.objects.create(
            application=application, referrer=self.patron,
            status="accepted", responded_at=timezone.now(),
        )
        approver, rule = recovery.resolve_approver(self.user)
        self.assertEqual(approver, self.patron_user)

    def test_nobody_resolvable_means_the_staff_queue(self):
        loner = User.objects.create_user("loner", password="pw")
        approver, rule = recovery.resolve_approver(loner)
        self.assertIsNone(approver)
        self.assertEqual(rule, RecoveryTicket.RULE_STAFF)


class TicketActionTests(RecoveryBase):
    def _approve_url(self, ticket):
        return reverse("sso:password_reset_ticket_approve", args=[ticket.pk])

    def _reject_url(self, ticket):
        return reverse("sso:password_reset_ticket_reject", args=[ticket.pk])

    def test_the_patron_approves_and_sees_the_link_exactly_once(self):
        ticket = self._file()
        self.client.force_login(self.patron_user)
        response = self.client.post(self._approve_url(ticket))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/password-reset/recover/")

        ticket.refresh_from_db()
        self.assertEqual(ticket.status, RecoveryTicket.APPROVED)
        self.assertEqual(ticket.approver, self.patron_user)
        self.assertEqual(len(ticket.link_sha256), 64)
        # Hashes only: no fragment of the minted link is in the row.
        link = response.context["link"]
        token = link.rstrip("/").rsplit("/", 1)[-1]
        self.assertNotIn(token, ticket.link_sha256)
        self.assertEqual(
            hashlib.sha256(token.encode()).hexdigest(), ticket.link_sha256,
        )

    def test_a_stranger_may_not_respond(self):
        ticket = self._file()
        stranger = User.objects.create_user("stranger", password="pw")
        self.client.force_login(stranger)
        self.assertEqual(self.client.post(self._approve_url(ticket)).status_code, 403)
        self.assertEqual(self.client.post(self._reject_url(ticket)).status_code, 403)

    def test_the_subject_may_not_approve_their_own_ticket(self):
        # A staff subject whose ticket landed in the staff queue: without the
        # self-exclusion, a hijacked staff session could mint itself a
        # password-change link without knowing the current password.
        staff_subject = User.objects.create_user(
            "staffer", email="s@x.test", password="pw", is_staff=True,
        )
        ticket = recovery.file_request("staffer")
        self.assertIsNone(ticket.approver)
        self.client.force_login(staff_subject)
        self.assertEqual(self.client.post(self._approve_url(ticket)).status_code, 403)

    def test_any_staff_member_may_claim_a_queue_ticket(self):
        loner = User.objects.create_user("loner", email="l@x.test", password="pw")
        ticket = recovery.file_request("loner")
        self.assertIsNone(ticket.approver)
        staff = User.objects.create_user("root", password="pw", is_staff=True)
        self.client.force_login(staff)
        response = self.client.post(self._approve_url(ticket))
        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.approver, staff)

    def test_reject_closes_the_ticket_without_minting_anything(self):
        ticket = self._file()
        self.client.force_login(self.patron_user)
        response = self.client.post(self._reject_url(ticket))
        self.assertEqual(response.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, RecoveryTicket.REJECTED)
        self.assertEqual(ticket.link_sha256, "")

    def test_an_expired_request_cannot_be_approved(self):
        ticket = self._file()
        RecoveryTicket.objects.filter(pk=ticket.pk).update(
            request_expires_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        self.client.force_login(self.patron_user)
        response = self.client.post(self._approve_url(ticket))
        self.assertEqual(response.status_code, 302)
        ticket.refresh_from_db()
        self.assertNotEqual(ticket.status, RecoveryTicket.APPROVED)

    def test_the_losing_staff_member_gets_a_bounce_not_a_403(self):
        # Two staff race one queue card; the winner becomes its approver, so
        # the loser fails may_respond — but a responder-shaped viewer of a
        # no-longer-pending ticket deserves "already handled", not a 403.
        loner = User.objects.create_user("loner", email="l@x.test", password="pw")
        ticket = recovery.file_request("loner")
        winner = User.objects.create_user("w", password="pw", is_staff=True)
        loser = User.objects.create_user("l", password="pw", is_staff=True)
        recovery.approve(ticket, winner)
        self.client.force_login(loser)
        response = self.client.post(self._approve_url(ticket))
        self.assertEqual(response.status_code, 302)

    def test_a_get_on_the_action_urls_bounces_instead_of_405(self):
        # Session expired mid-approve: the POST bounces through login and is
        # replayed as a GET. A 405 there is a dead end; the profile — where
        # the card still is — is the answer.
        ticket = self._file()
        self.client.force_login(self.patron_user)
        response = self.client.get(self._approve_url(ticket))
        self.assertEqual(response.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, RecoveryTicket.PENDING)

    def test_the_lifecycle_is_audited(self):
        if not django_apps.is_installed("toto.audit"):
            self.skipTest("audit not installed on this host")
        from toto.audit.models import AuditRecord

        ticket = self._file()
        self.client.force_login(self.patron_user)
        self.client.post(self._reject_url(ticket))
        actions = set(AuditRecord.objects.values_list("action", flat=True))
        self.assertIn("PASSWORD_RECOVERY_REQUESTED", actions)
        self.assertIn("PASSWORD_RECOVERY_REJECTED", actions)


class RedeemTests(RecoveryBase):
    def _approved_link(self):
        ticket = self._file()
        token = recovery.approve(ticket, self.patron_user)
        return ticket, reverse("sso:password_reset_recover", args=[token])

    def test_a_valid_link_shows_the_new_password_form(self):
        ticket, url = self._approved_link()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["validlink"])
        self.assertTemplateUsed(response, "sso/password_reset_confirm.html")

    def test_the_password_changes_and_the_link_burns(self):
        ticket, url = self._approved_link()
        response = self.client.post(url, {
            "new_password1": "brand-new-passw0rd",
            "new_password2": "brand-new-passw0rd",
        })
        self.assertRedirects(response, reverse("sso:password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("brand-new-passw0rd"))
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, RecoveryTicket.USED)
        self.assertIsNotNone(ticket.used_at)
        # Single use: the same link is now an invalid-link page.
        self.assertFalse(self.client.get(url).context["validlink"])

    def test_a_mismatched_form_burns_nothing(self):
        ticket, url = self._approved_link()
        response = self.client.post(url, {
            "new_password1": "one-thing", "new_password2": "another-thing",
        })
        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, RecoveryTicket.APPROVED)

    def test_an_expired_link_is_invalid_and_flips_the_ticket(self):
        ticket, url = self._approved_link()
        RecoveryTicket.objects.filter(pk=ticket.pk).update(
            link_expires_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        response = self.client.get(url)
        self.assertFalse(response.context["validlink"])
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, RecoveryTicket.EXPIRED)

    def test_a_garbage_token_is_an_invalid_link_not_an_error(self):
        url = reverse("sso:password_reset_recover", args=[uuid.uuid4()])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["validlink"])

    def test_use_is_audited(self):
        if not django_apps.is_installed("toto.audit"):
            self.skipTest("audit not installed on this host")
        from toto.audit.models import AuditRecord

        ticket, url = self._approved_link()
        self.client.post(url, {
            "new_password1": "brand-new-passw0rd",
            "new_password2": "brand-new-passw0rd",
        })
        self.assertTrue(
            AuditRecord.objects.filter(action="PASSWORD_RECOVERY_USED").exists()
        )

    def test_the_plain_token_never_reaches_the_audit_trail(self):
        # request_source captures the request PATH — which for the recover
        # route carries the bearer token. The audit layer scrubs UUID-shaped
        # segments precisely so this row can never become the durable copy of
        # the secret ("a database dump yields nothing redeemable").
        if not django_apps.is_installed("toto.audit"):
            self.skipTest("audit not installed on this host")
        import json

        from toto.audit.models import AuditRecord

        ticket, url = self._approved_link()
        token = url.rstrip("/").rsplit("/", 1)[-1]
        self.client.post(url, {
            "new_password1": "brand-new-passw0rd",
            "new_password2": "brand-new-passw0rd",
        })
        record = AuditRecord.objects.get(action="PASSWORD_RECOVERY_USED")
        self.assertNotIn(token, json.dumps(record.request_source))
        self.assertNotIn(token, json.dumps(record.changes))

    def test_a_sweep_names_no_actor_even_mid_page_render(self):
        # The expiry fires during whatever page render touched the ticket;
        # the browsing user must not be recorded as the actor of it.
        if not django_apps.is_installed("toto.audit"):
            self.skipTest("audit not installed on this host")
        from toto.audit.models import AuditRecord

        ticket = self._file()
        token = recovery.approve(ticket, self.patron_user)
        RecoveryTicket.objects.filter(pk=ticket.pk).update(
            link_expires_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        self.client.force_login(self.patron_user)
        self.client.get(
            reverse("socialhub:profile_details", args=[self.patron.slug])
        )
        record = AuditRecord.objects.get(action="PASSWORD_RECOVERY_EXPIRED")
        self.assertIsNone(record.actor_user_id)

    def test_expiry_is_audited_once(self):
        if not django_apps.is_installed("toto.audit"):
            self.skipTest("audit not installed on this host")
        from toto.audit.models import AuditRecord

        ticket, url = self._approved_link()
        RecoveryTicket.objects.filter(pk=ticket.pk).update(
            link_expires_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        self.client.get(url)
        recovery.sweep_expired()
        self.assertEqual(
            AuditRecord.objects.filter(action="PASSWORD_RECOVERY_EXPIRED").count(), 1,
        )


class ProfileCardTests(RecoveryBase):
    """The ticket card renders on the APPROVER's own profile and nowhere else
    — the community-invitation shape, with the same two POST forms."""

    def _profile_url(self, person):
        return reverse("socialhub:profile_details", args=[person.slug])

    def test_the_patron_sees_the_card_on_their_own_profile(self):
        self._file()
        self.client.force_login(self.patron_user)
        response = self.client.get(self._profile_url(self.patron))
        self.assertContains(response, "Password Recovery Requests")
        self.assertContains(response, "mentee")
        self.assertContains(response, "You are their patron")
        self.assertContains(response, "/password-reset/ticket/")

    def test_a_visitor_to_the_same_profile_sees_nothing(self):
        self._file()
        visitor = User.objects.create_user("visitor", password="pw")
        _person(visitor, "Visitor")
        self.client.force_login(visitor)
        response = self.client.get(self._profile_url(self.patron))
        self.assertNotContains(response, "Password Recovery Requests")

    def test_no_tickets_means_no_section(self):
        self.client.force_login(self.patron_user)
        response = self.client.get(self._profile_url(self.patron))
        self.assertNotContains(response, "Password Recovery Requests")

    def test_a_staff_profile_carries_the_queue(self):
        loner = User.objects.create_user("loner", email="l@x.test", password="pw")
        recovery.file_request("loner")
        staff = User.objects.create_user("root", password="pw", is_staff=True)
        staff_person = _person(staff, "Root")
        self.client.force_login(staff)
        response = self.client.get(self._profile_url(staff_person))
        self.assertContains(response, "Staff queue")

    def test_an_expired_request_never_renders_as_actionable(self):
        ticket = self._file()
        RecoveryTicket.objects.filter(pk=ticket.pk).update(
            request_expires_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        self.client.force_login(self.patron_user)
        response = self.client.get(self._profile_url(self.patron))
        self.assertNotContains(response, "Password Recovery Requests")
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, RecoveryTicket.EXPIRED)


@override_settings(EMAIL_BACKEND=CONSOLE, RESET_REQUEST_COOLDOWN_SECONDS=0)
class InlineEmailFlowTests(RecoveryBase):
    """Flow 1's inline branch: a non-delivering backend, no stored secret, but
    this process holds the credential — so the EMAIL form serves, and the mail
    leaves in the request itself with an outbox row to show for it."""

    def setUp(self):
        super().setUp()
        if not django_apps.is_installed("toto.jess"):
            self.skipTest("jess not installed on this host")
        from toto.jess import credentials
        from toto.jess.models import EmailProvider

        credentials.lock_all()
        self.addCleanup(credentials.lock_all)
        EmailProvider.objects.create(
            label="Relay", backend="smtp", host="smtp.example.org",
            username="mailer", active=True,
        )
        credentials.unlock("staff-session", "hunter2")

    def test_the_email_form_serves_while_the_process_is_unlocked(self):
        response = self.client.get(reverse("sso:password_reset"))
        self.assertContains(response, "Send Reset Link")
        self.assertNotContains(response, "Request Recovery")

    def test_the_reset_email_sends_inline_with_an_outbox_row(self):
        from django.core.mail import get_connection

        from toto.jess.models import MailMessage

        locmem = get_connection(LOCMEM)
        with patch("toto.jess.delivery.build_connection",
                   return_value=locmem) as build:
            response = self.client.post(
                reverse("sso:password_reset"), {"email": self.user.email},
            )
        self.assertRedirects(response, reverse("sso:password_reset_done"))
        build.assert_called_once()
        # The credential travelled as an argument, never through a store.
        self.assertEqual(build.call_args.kwargs.get("password"), "hunter2")

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/password-reset/", mail.outbox[0].body)

        row = MailMessage.objects.get()
        self.assertEqual(row.status, MailMessage.SENT)
        self.assertEqual(row.purpose, MailMessage.PURPOSE_PASSWORD_RESET)
        self.assertTrue(row.body_is_sensitive)

    def test_locking_the_process_falls_back_to_the_ticket_flow(self):
        from toto.jess import credentials

        credentials.lock_all()
        response = self.client.get(reverse("sso:password_reset"))
        self.assertContains(response, "Request Recovery")


class RankRuleTests(RecoveryBase):
    """An approver may never recover somebody who outranks them.

    THE HOLE THIS CLOSES, in the three clicks it took: until 2026-09-07 any
    staff member could file a recovery for a SUPERUSER on the public reset
    page, see it land in their own staff queue (`approver` NULL), approve it,
    and be handed the one-time link by `approve()`. `file_request` refused
    nobody and `may_respond` asked only `is_staff or is_superuser`. Staff to
    superuser, with the audit chain recording a routine recovery.
    """

    def _approve_url(self, ticket):
        return reverse("sso:password_reset_ticket_approve", args=[ticket.pk])

    def _reject_url(self, ticket):
        return reverse("sso:password_reset_ticket_reject", args=[ticket.pk])

    def test_a_staff_member_cannot_approve_a_superuser_recovery(self):
        root = User.objects.create_user(
            "root", email="root@x.test", password="pw",
            is_staff=True, is_superuser=True,
        )
        ticket = recovery.file_request("root")
        self.assertIsNone(ticket.approver, "should be a queue ticket")

        staff = User.objects.create_user(
            "hired", email="hired@x.test", password="pw", is_staff=True,
        )
        self.client.force_login(staff)
        self.assertEqual(
            self.client.post(self._approve_url(ticket)).status_code, 403)

        # And the ticket is untouched — no link minted, still claimable by
        # somebody who may.
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, RecoveryTicket.PENDING)
        self.assertEqual(ticket.link_sha256, "")
        self.assertIsNone(ticket.approver)

    def test_a_staff_member_cannot_reject_one_either(self):
        """Rejecting is not minting, but it IS denial of service against the
        one account that can undo everything: a staff member who could close
        the manager's ticket at will could keep them locked out."""
        User.objects.create_user(
            "root", email="root@x.test", password="pw",
            is_staff=True, is_superuser=True,
        )
        ticket = recovery.file_request("root")
        staff = User.objects.create_user(
            "hired", email="hired@x.test", password="pw", is_staff=True,
        )
        self.client.force_login(staff)
        self.assertEqual(
            self.client.post(self._reject_url(ticket)).status_code, 403)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, RecoveryTicket.PENDING)

    def test_a_superuser_may_approve_another_superuser(self):
        """The rule is 'at least', not 'above' — otherwise the top rank could
        never be recovered by anybody."""
        User.objects.create_user(
            "root", email="root@x.test", password="pw",
            is_staff=True, is_superuser=True,
        )
        ticket = recovery.file_request("root")
        other_root = User.objects.create_user(
            "root2", email="root2@x.test", password="pw",
            is_staff=True, is_superuser=True,
        )
        self.client.force_login(other_root)
        self.assertEqual(
            self.client.post(self._approve_url(ticket)).status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.approver, other_root)

    def test_the_superuser_ticket_is_not_even_on_a_staff_queue(self):
        """A card the viewer would be refused at invites a click that 403s."""
        User.objects.create_user(
            "root", email="root@x.test", password="pw",
            is_staff=True, is_superuser=True,
        )
        ticket = recovery.file_request("root")
        staff = User.objects.create_user(
            "hired", email="hired@x.test", password="pw", is_staff=True,
        )
        self.assertNotIn(ticket, recovery.tickets_for_approver(staff))

        root2 = User.objects.create_user(
            "root2", email="root2@x.test", password="pw",
            is_staff=True, is_superuser=True,
        )
        self.assertIn(ticket, recovery.tickets_for_approver(root2))

    def test_an_ordinary_patron_does_not_become_a_staff_members_route(self):
        """A personal approver who may not approve is SKIPPED, and the ticket
        falls through to the queue — it does not silently name them."""
        staff_user = User.objects.create_user(
            "manager", email="manager@x.test", password="pw", is_staff=True,
        )
        _person(staff_user, "Manager", patron=self.patron)

        ticket = recovery.file_request("manager")
        self.assertIsNone(ticket.approver)
        self.assertEqual(ticket.approver_rule, RecoveryTicket.RULE_STAFF)

        self.client.force_login(self.patron_user)
        self.assertEqual(
            self.client.post(self._approve_url(ticket)).status_code, 403)

    def test_an_ordinary_member_is_still_recovered_by_their_patron(self):
        """The floor: the ordinary flow this module exists for is untouched."""
        ticket = self._file()
        self.assertEqual(ticket.approver, self.patron_user)
        self.client.force_login(self.patron_user)
        self.assertEqual(
            self.client.post(self._approve_url(ticket)).status_code, 200)

    @override_settings(RECOVERY_PROTECTED_GROUPS=["operations"])
    def test_a_protected_group_member_needs_a_badge(self):
        """The host-nameable rank: zenobia grades its operators by group, so a
        group member may not be recovered by an ordinary patron even though
        neither carries a Django flag."""
        from django.contrib.auth.models import Group

        operations = Group.objects.create(name="operations")
        operator = User.objects.create_user(
            "operator", email="op@x.test", password="pw",
        )
        operator.groups.add(operations)
        _person(operator, "Operator", patron=self.patron)

        ticket = recovery.file_request("operator")
        self.assertIsNone(ticket.approver, "the patron may not approve them")

        self.client.force_login(self.patron_user)
        self.assertEqual(
            self.client.post(self._approve_url(ticket)).status_code, 403)
        self.assertNotIn(ticket, recovery.tickets_for_approver(self.patron_user))

        staff = User.objects.create_user(
            "manager", email="manager@x.test", password="pw", is_staff=True,
        )
        self.client.force_login(staff)
        self.assertEqual(
            self.client.post(self._approve_url(ticket)).status_code, 200)

    def test_a_named_approver_is_rechecked_at_approval_time(self):
        """The ticket may have been filed when the subject was an ordinary
        member. Promotion must not leave a live ticket naming somebody who may
        no longer act on it."""
        ticket = self._file()
        self.assertEqual(ticket.approver, self.patron_user)

        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])

        self.client.force_login(self.patron_user)
        self.assertEqual(
            self.client.post(self._approve_url(ticket)).status_code, 403)
