"""The invitation's way in (2026-10-10): ``enrollment.py``, ``invitations.py``.

A platform chooses how people get an account (``SOCIALHUB_ENROLLMENT``): by
an application a patron accepts, or by an invitation staff issue. Either way
ends in the same thing, a person in a community (``enrollment.admit``), and
the other way's doors are closed.

    manage.py test toto.socialhub.tests_invitations

A host that runs these lists ``socialhub:invitation_accept`` among its public
routes, as it lists the application's.
"""
from __future__ import annotations

import io
import json
import re
from datetime import timedelta
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.checks import run_checks
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform
from toto.people.models import Person

from . import enrollment, invitations
from .models import (Community, Invitation, MembershipApplication, PrivacyAcceptance,
                     PrivacyNotice, ReferenceRequest)

User = get_user_model()
PASSWORD = "Correct-horse-9x"


def _actions():
    if not apps.is_installed("toto.audit"):
        return None
    from toto.audit.models import AuditRecord

    return list(AuditRecord.objects.order_by("sequence").values_list("action", flat=True))


@override_settings(SOCIALHUB_ENROLLMENT="invitation")
class InvitationCase(TestCase):
    def setUp(self):
        cache.clear()
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        self.guild = Community.objects.create(name="Cedar Guild", slug="cedar")
        self.notice = PrivacyNotice.objects.create(version=1, text_pl="Informacja",
                                                   text_en="Notice")
        self.staff = User.objects.create_user("ops", password=PASSWORD, is_staff=True)
        self.staff_person = Person.objects.create(user=self.staff, display_name="Ops")
        self.member = User.objects.create_user("mia", email="mia@example.com", password=PASSWORD)
        Person.objects.create(user=self.member, display_name="Mia")

    def as_(self, user):
        client = Client()
        client.force_login(user)
        return client

    def issue(self, **data):
        """Issue through the page; ``(response, token)``."""
        response = self.as_(self.staff).post(reverse("socialhub:invitations"),
                                             {"community": self.guild.pk, **data})
        found = re.search(r'data-testid="invitation-link">([^<]+)<', response.content.decode())
        token = found.group(1).strip().rstrip("/").rsplit("/", 1)[1] if found else ""
        return response, token

    def accept(self, token, client=None, **extra):
        data = {"username": "nina", "display_name": "Nina K", "password1": PASSWORD,
                "password2": PASSWORD, "privacy_version": self.notice.version,
                "privacy_accept": "on", **extra}
        return (client or Client()).post(reverse("socialhub:invitation_accept", args=[token]),
                                         {k: v for k, v in data.items() if v is not None})


class IssuingTests(InvitationCase):
    def test_staff_issue_a_link_shown_once_and_mailed_to_the_address(self):
        response, token = self.issue(email="nina@example.com", note="Nina, accounts")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(token)
        row = Invitation.objects.get()
        self.assertEqual((row.email, row.community, row.note, row.issued_by, row.state),
                         ("nina@example.com", self.guild, "Nina, accounts", self.staff, "open"))
        # The token is in the link and the mail, never in the row.
        self.assertNotIn(token, json.dumps(list(Invitation.objects.values("token_hash", "note"))))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["nina@example.com"])
        self.assertIn(token, mail.outbox[0].body)
        self.assertContains(response, 'data-testid="invitation-mailed"')
        # Shown once: the list has the row, not its link.
        again = self.as_(self.staff).get(reverse("socialhub:invitations"))
        self.assertNotContains(again, token)
        self.assertContains(again, "nina@example.com")

    def test_without_an_address_nothing_is_mailed(self):
        response, token = self.issue()
        self.assertTrue(token)
        self.assertEqual(mail.outbox, [])
        self.assertNotContains(response, 'data-testid="invitation-mailed"')

    def test_a_community_is_required_and_an_address_in_use_is_refused(self):
        client = self.as_(self.staff)
        response = client.post(reverse("socialhub:invitations"), {"note": "x"})
        self.assertIn("community", response.context["form"].errors)
        response = client.post(reverse("socialhub:invitations"),
                               {"community": self.guild.pk, "email": "MIA@example.com"})
        self.assertIn("email", response.context["form"].errors)
        self.assertFalse(Invitation.objects.exists())

    def test_the_answer_that_carries_the_link_is_never_stored(self):
        response, _token = self.issue()
        self.assertIn("no-store", response["Cache-Control"])

    def test_a_mail_that_cannot_be_sent_still_shows_the_link(self):
        with mock.patch("toto.socialhub.invitations.EmailMessage.send", side_effect=OSError):
            response, token = self.issue(email="nina@example.com")
        self.assertTrue(token)
        self.assertContains(response, 'data-testid="invitation-not-mailed"')

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_a_platform_without_mail_delivery_does_not_say_it_mailed(self):
        """A console backend would print the link (the token with it) and
        the page would say "mailed"."""
        with mock.patch("toto.socialhub.invitations.EmailMessage.send") as send:
            response, token = self.issue(email="nina@example.com")
        send.assert_not_called()
        self.assertTrue(token)
        self.assertContains(response, 'data-testid="invitation-not-mailed"')

    def test_only_staff_issue_list_and_revoke(self):
        _response, token = self.issue()
        row = Invitation.objects.get()
        member = self.as_(self.member)
        self.assertEqual(member.get(reverse("socialhub:invitations")).status_code, 403)
        self.assertEqual(member.post(reverse("socialhub:invitations"),
                                     {"community": self.guild.pk}).status_code, 403)
        self.assertEqual(member.post(reverse("socialhub:invitation_revoke", args=[row.pk]))
                         .status_code, 403)
        self.assertEqual(Invitation.objects.count(), 1)
        # The tab shows to whom its doors open.
        self.assertNotContains(member.get(reverse("socialhub:profile_list")), "tab-invitations")
        self.assertContains(self.as_(self.staff).get(reverse("socialhub:profile_list")),
                            "tab-invitations")
        self.assertEqual(self.as_(self.staff).get(
            reverse("socialhub:invitation_revoke", args=[row.pk])).status_code, 405)
        self.as_(self.staff).post(reverse("socialhub:invitation_revoke", args=[row.pk]))
        row.refresh_from_db()
        self.assertEqual(row.state, "revoked")
        self.assertEqual(Client().get(reverse("socialhub:invitation_accept", args=[token]))
                         .status_code, 404)


class AcceptingTests(InvitationCase):
    def test_the_account_is_a_person_in_the_community_as_an_admission_makes_it(self):
        _response, token = self.issue(email="nina@example.com")
        page = Client().get(reverse("socialhub:invitation_accept", args=[token]))
        self.assertContains(page, reverse("socialhub:privacy_notice_version", args=[1]))
        self.assertContains(page, 'name="privacy_version" value="1"')
        self.assertContains(page, "Cedar Guild")
        client = Client()
        response = self.accept(token, client)
        self.assertRedirects(response, reverse("core:dashboard"), fetch_redirect_response=False)
        user = User.objects.get(username="nina")
        person = user.community_profile
        self.assertTrue(user.is_active and user.check_password(PASSWORD))
        self.assertEqual((user.email, person.display_name, person.email),
                         ("nina@example.com", "Nina K", "nina@example.com"))
        self.assertEqual(list(person.communities.all()), [self.guild])
        self.assertEqual(person.patron, self.staff_person)       # who issued it vouched
        self.assertEqual(list(PrivacyAcceptance.objects.filter(person=person)
                              .values_list("version", flat=True)), [1])
        row = Invitation.objects.get()
        self.assertEqual((row.state, row.accepted_user), ("accepted", user))
        self.assertEqual(int(client.session["_auth_user_id"]), user.pk)      # signed in
        # Spent: the same link makes nothing more.
        self.assertEqual(self.accept(token, username="second").status_code, 404)
        self.assertFalse(User.objects.filter(username="second").exists())

    def test_a_link_that_is_not_open_is_one_page_whatever_it_was(self):
        _response, token = self.issue()
        Invitation.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        for bad in (token, "no-such-token", "x" * 300):
            response = Client().get(reverse("socialhub:invitation_accept", args=[bad]))
            self.assertEqual(response.status_code, 404, bad[:20])
            self.assertContains(response, 'data-testid="invitation-closed"', status_code=404)
        self.assertEqual(self.accept(token).status_code, 404)
        self.assertFalse(User.objects.filter(username="nina").exists())

    def test_the_address_the_link_was_issued_to_is_not_a_choice(self):
        _response, token = self.issue(email="nina@example.com")
        self.accept(token, email="other@example.com")
        self.assertEqual(User.objects.get(username="nina").email, "nina@example.com")

    def test_refusals_make_no_account(self):
        _response, token = self.issue()
        User.objects.create_user("taken", email="held@example.com", password=PASSWORD)
        for why, field, extra in (
                ("no tick", "privacy_accept", {"privacy_accept": None}),
                ("a stale version", "privacy_accept", {"privacy_version": 7}),
                ("a taken username", "username", {"username": "TAKEN"}),
                ("another account's address as the username", "username",
                 {"username": "held@example.com"}),
                ("an address in use", "email", {"email": "HELD@example.com"}),
                ("passwords that differ", "password2", {"password2": PASSWORD + "x"}),
                ("a weak password", "password1",
                 {"password1": "12345678", "password2": "12345678"})):
            response = self.accept(token, **extra)
            self.assertEqual(response.status_code, 400, why)
            self.assertIn(field, response.context["form"].errors, why)
            self.assertEqual(User.objects.count(), 3, why)
        self.assertEqual(Invitation.objects.get().state, "open")

    def test_a_stale_version_shows_the_current_one_and_asks_again(self):
        _response, token = self.issue()
        PrivacyNotice.objects.create(version=2, text_pl="Nowa", text_en="New")
        response = self.accept(token)                     # carries version 1
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'name="privacy_version" value="2"', status_code=400)
        self.assertContains(response, reverse("socialhub:privacy_notice_version", args=[2]),
                            status_code=400)

    def test_no_notice_no_account(self):
        _response, token = self.issue()
        PrivacyNotice.objects.all().delete()
        page = Client().get(reverse("socialhub:invitation_accept", args=[token]))
        self.assertContains(page, 'data-testid="invitation-no-notice"')
        self.assertEqual(self.accept(token).status_code, 400)
        self.assertFalse(User.objects.filter(username="nina").exists())

    def test_somebody_signed_in_makes_no_second_account(self):
        _response, token = self.issue()
        response = self.accept(token, self.as_(self.member))
        self.assertRedirects(response, reverse("core:dashboard"), fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(username="nina").exists())

    def test_the_page_is_rate_limited_by_address(self):
        from .views.invitations import ACCEPTS_PER_ADDRESS

        _response, token = self.issue()
        client = Client()
        missing = reverse("socialhub:invitation_accept", args=["no-such-token"])
        opened = reverse("socialhub:invitation_accept", args=[token])
        with mock.patch("toto.core.ratelimit._bucket", return_value=1):   # one window, whatever the clock
            # Misses are counted by address...
            for _ in range(ACCEPTS_PER_ADDRESS):
                self.assertEqual(client.get(missing).status_code, 404)
            self.assertEqual(client.get(missing).status_code, 429)
            # ...and an open link still opens from that address: the people
            # of one office are not locked out by one another.
            self.assertEqual(client.get(opened).status_code, 200)
            # Its form is counted by link.
            for _ in range(ACCEPTS_PER_ADDRESS):
                self.assertEqual(self.accept(token, username="mia").status_code, 400)
            self.assertEqual(self.accept(token).status_code, 429)
        self.assertFalse(User.objects.filter(username="nina").exists())

    def test_the_chain_names_the_invitation_and_never_its_address(self):
        if _actions() is None:
            self.skipTest("toto.audit is not installed")
        from toto.audit.models import AuditRecord

        _response, token = self.issue(email="nina@example.com")
        self.accept(token)
        actions = [a.upper() for a in _actions()]
        for action in ("SOCIALHUB.INVITATION_ISSUED", "SOCIALHUB.INVITATION_ACCEPTED",
                       "PRIVACY.NOTICE_ACCEPTED"):
            self.assertIn(action, actions)
        rows = json.dumps(list(AuditRecord.objects.values()), default=str)     # every column
        self.assertNotIn("nina@example.com", rows)
        self.assertNotIn(token, rows)

    def test_a_link_closed_while_the_form_was_sent_makes_no_account(self):
        _response, token = self.issue()
        real = Invitation.open_for.__func__

        def stale(cls, value):
            row = real(cls, value)
            cls.objects.filter(pk=row.pk).update(revoked_at=timezone.now())
            return row

        with mock.patch.object(Invitation, "open_for", classmethod(stale)):
            response = self.accept(token)
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'data-testid="invitation-closed"', status_code=404)
        self.assertFalse(User.objects.filter(username="nina").exists())

    def test_an_issuer_who_is_no_member_leaves_no_patron(self):
        self.staff_person.delete()
        _response, token = self.issue()
        self.accept(token)
        person = User.objects.get(username="nina").community_profile
        self.assertIsNone(person.patron)
        self.assertEqual(list(person.communities.all()), [self.guild])

    def test_a_link_stops_working_when_its_issuer_is_no_longer_staff(self):
        _response, token = self.issue()
        User.objects.filter(pk=self.staff.pk).update(is_staff=False)
        self.assertEqual(Client().get(reverse("socialhub:invitation_accept", args=[token]))
                         .status_code, 404)
        self.assertEqual(self.accept(token).status_code, 404)
        self.assertFalse(User.objects.filter(username="nina").exists())
        # One whose issuer is gone altogether stays the platform's.
        User.objects.filter(pk=self.staff.pk).update(is_staff=True)
        Invitation.objects.update(issued_by=None)
        self.assertEqual(self.accept(token).status_code, 302)

    def test_no_second_account_at_an_address_somebody_holds(self):
        """Two invitations may name one address; the second makes nothing."""
        _response, first = self.issue(email="nina@example.com")
        _response, second = self.issue(email="nina@example.com")
        self.assertEqual(self.accept(first).status_code, 302)
        response = self.accept(second, username="nina2")
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.context["form"].errors)
        self.assertEqual(User.objects.filter(email__iexact="nina@example.com").count(), 1)

    def test_an_address_another_invitation_is_made_out_to_cannot_be_typed(self):
        """Nothing on the page proves the person typing an address reads its
        mail: the holder of an address-less link does not get somebody
        else's."""
        self.issue(email="vera@example.com", note="Vera, the lawyer")
        _response, bare = self.issue()
        response = self.accept(bare, username="mal", email="VERA@example.com")
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="mal").exists())
        self.assertEqual(self.accept(bare, username="mal", email="mal@example.com").status_code,
                         302)


class LifecycleTests(InvitationCase):
    def test_a_members_invitations_are_in_their_copy_and_go_with_their_account(self):
        from django.core.management import call_command

        from toto.core.personal_data import tables_for

        _response, token = self.issue(email="nina@example.com")
        self.accept(token)
        user = User.objects.get(username="nina")
        table = {t.name: t.rows for t in tables_for(user)}["invitations"]
        self.assertEqual(len(table), 1)
        self.assertNotIn("token_hash", table[0])
        self.assertNotIn("issued_by_id", table[0])
        self.assertEqual(table[0]["community"], "Cedar Guild")
        # Not another member's.
        self.assertNotIn("invitations", {t.name for t in tables_for(self.member)})
        # The erase's plan counts it once, as erased, not as kept without them.
        from toto.core.management.commands.erase_user import plan

        report = plan(user)
        self.assertEqual(report["beyond"]["invitations"], 1)
        self.assertNotIn("socialhub.Invitation.accepted_user", report["detached"])
        call_command("erase_user", "nina", confirm="nina", verbosity=0, stdout=io.StringIO())
        self.assertFalse(Invitation.objects.exists())

    def test_an_accepted_invitation_is_the_accepting_accounts_alone(self):
        """Matched by the account once accepted, by the address only before:
        a later holder of the address neither reads nor erases it."""
        _response, token = self.issue(email="nina@example.com", note="Nina, accounts")
        self.accept(token)
        nina = User.objects.get(username="nina")
        User.objects.filter(pk=self.member.pk).update(email="nina@example.com")   # forced
        self.member.refresh_from_db()
        self.assertEqual(list(invitations.of_member(self.member)), [])
        self.assertEqual(invitations.of_member(nina).count(), 1)
        # One still waiting for that address is theirs to see.
        pending, _t = invitations.issue(issued_by=self.staff, community=self.guild,
                                        email="mia2@example.com")
        User.objects.filter(pk=self.member.pk).update(email="MIA2@example.com")
        self.member.refresh_from_db()
        self.assertEqual(list(invitations.of_member(self.member)), [pending])

    @override_settings(SOCIALHUB_EXPIRED_INVITATION_DAYS=30)
    def test_the_housekeeping_deletes_what_expired_long_ago_and_only_that(self):
        from toto.core import housekeeping

        fresh, _t = invitations.issue(issued_by=self.staff, community=self.guild)
        old, _t = invitations.issue(issued_by=self.staff, community=self.guild)
        Invitation.objects.filter(pk=old.pk).update(
            expires_at=timezone.now() - timedelta(days=31))
        self.assertEqual(housekeeping.run()["invitations_pruned"], 1)
        self.assertEqual(list(Invitation.objects.all()), [fresh])
        with override_settings(SOCIALHUB_EXPIRED_INVITATION_DAYS=0):
            Invitation.objects.update(expires_at=timezone.now() - timedelta(days=400))
            self.assertEqual(invitations.prune(), {"pruned": 0})

    def test_a_removed_community_closes_its_invitations(self):
        _response, token = self.issue()
        self.guild.delete()
        row = Invitation.objects.get()
        self.assertFalse(row.is_open)
        self.assertEqual(Client().get(reverse("socialhub:invitation_accept", args=[token]))
                         .status_code, 404)


class EachPlatformItsWayTests(TestCase):
    def setUp(self):
        cache.clear()
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        self.guild = Community.objects.create(name="Cedar Guild", slug="cedar")
        PrivacyNotice.objects.create(version=1, text_pl="Informacja", text_en="Notice")
        self.staff = User.objects.create_user("ops", password=PASSWORD, is_staff=True)

    def test_applications_are_the_way_unless_the_platform_says_otherwise(self):
        from django.conf import settings

        with override_settings():
            # Whatever the host running this chose: the library's own default.
            if hasattr(settings, "SOCIALHUB_ENROLLMENT"):
                del settings.SOCIALHUB_ENROLLMENT
            self.assertEqual(enrollment.way(), "application")
            self._the_application_way_is_open()

    def _the_application_way_is_open(self):
        staff = Client()
        staff.force_login(self.staff)
        self.assertEqual(staff.get(reverse("socialhub:invitations")).status_code, 404)
        _row, token = invitations.issue(issued_by=self.staff, community=self.guild)
        self.assertEqual(Client().get(reverse("socialhub:invitation_accept", args=[token]))
                         .status_code, 404)                 # a real, open link: the door is closed
        self.assertNotContains(staff.get(reverse("socialhub:profile_list")), "tab-invitations")
        self.assertEqual(Client().get(reverse("socialhub:membership_application")).status_code, 200)

    @override_settings(SOCIALHUB_ENROLLMENT="invitation")
    def test_a_platform_that_invites_takes_no_applications(self):
        for name in ("socialhub:membership_application", "socialhub:application_success",
                     "socialhub:membership_verification", "socialhub:application_verified",
                     "socialhub:reference_request", "socialhub:reference_next"):
            self.assertEqual(Client().get(reverse(name)).status_code, 404, name)
        # Nor does a reference still waiting admit anybody: the answering
        # doors are closed too.
        patron = Person.objects.create(
            user=User.objects.create_user("pat", password=PASSWORD), display_name="Pat")
        User.objects.create_user("app", email="app@example.com", password=PASSWORD,
                                 is_active=False)
        application = MembershipApplication.objects.create(
            email="app@example.com", community=self.guild,
            expires_at=timezone.now() + timedelta(days=7))
        reference = ReferenceRequest.objects.create(application=application, referrer=patron)
        member = Client()
        member.force_login(patron.user)
        for name in ("socialhub:reference_accept", "socialhub:reference_reject"):
            self.assertEqual(member.post(reverse(name, args=[reference.pk])).status_code, 404, name)
        reference.refresh_from_db()
        self.assertEqual(reference.status, "pending")
        self.assertFalse(User.objects.get(username="app").is_active)

    def test_a_way_nobody_knows_is_a_system_check_error(self):
        with override_settings(SOCIALHUB_ENROLLMENT="both"):
            self.assertIn("socialhub.E001", [e.id for e in run_checks()])
        with override_settings(SOCIALHUB_ENROLLMENT="invitation"):
            self.assertNotIn("socialhub.E001", [e.id for e in run_checks()])

    def test_both_ways_end_in_the_same_person_in_the_community(self):
        """The patron's acceptance and the invitation call one function."""
        patron = Person.objects.create(
            user=User.objects.create_user("pat", password=PASSWORD), display_name="Pat")
        # By application: a referrer accepts.
        applicant = User.objects.create_user("app", email="app@example.com", password=PASSWORD,
                                             is_active=False)
        application = MembershipApplication.objects.create(
            email="app@example.com", community=self.guild,
            expires_at=timezone.now() + timedelta(days=7),
            privacy_version=1, privacy_accepted_at=timezone.now())
        reference = ReferenceRequest.objects.create(application=application, referrer=patron)
        reference.status = "accepted"
        reference.save()
        # By invitation: the same member issues it.
        with override_settings(SOCIALHUB_ENROLLMENT="invitation"):
            row, _token = invitations.issue(issued_by=patron.user, community=self.guild)
            invited, _person = invitations.accept(
                row, username="inv", display_name="inv", email="inv@example.com",
                password=PASSWORD, privacy_version=1)

        def outcome(user):
            user.refresh_from_db()
            person = user.community_profile
            return (user.is_active, person.display_name == user.username,
                    person.email == user.email, list(person.communities.all()), person.patron,
                    list(PrivacyAcceptance.objects.filter(person=person)
                         .values_list("version", flat=True)))

        self.assertEqual(outcome(applicant), outcome(invited))
        self.assertEqual(outcome(invited), (True, True, True, [self.guild], patron, [1]))
