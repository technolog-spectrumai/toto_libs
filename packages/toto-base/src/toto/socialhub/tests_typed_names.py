"""Whose failed sign-in is it (2026-10-02, the crown bug hunt).

"Recent sign-ins" on My account shows a member the failed sign-ins made at
their account, with the address and browser of whoever made them, and the
data export lists them. A failed sign-in names no account — only what was
typed — and every account whose username OR e-mail address matched that,
in any case, was given it. Usernames are free text, so one account's
username can be another's address: then each saw the other's failed
sign-ins, and the approval mail, which told new members to sign in "using
your email address" (sign-in is by username), sent them to type exactly
that.

Now a failed sign-in is ONE account's, decided when it is recorded: the
account the sign-in looked the name up as (its username, as typed); else
the one account with that username in another case; else the one account
with that e-mail address. A name nobody has, or several share, is nobody's.
The approval mail names the username to sign in with.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.socialhub.tests_typed_names
"""

from __future__ import annotations

import re
from datetime import timedelta

from django.contrib.auth import authenticate, get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from toto.audit import identity
from toto.audit.queries import member_auth_records, records_about
from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Community, MembershipApplication, ReferenceRequest
from toto.socialhub.views.account import _signin_row

User = get_user_model()


def fail(username, *, address="198.51.100.23", browser="Guesser/1.0"):
    """A wrong password typed at the sign-in, from ``address``."""
    request = RequestFactory().post("/sso/login/", REMOTE_ADDR=address, HTTP_USER_AGENT=browser)
    assert authenticate(request, username=username, password="not-the-password") is None


class TypedNameCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        # Bob signs in as "bob". Eve chose Bob's address as her username.
        self.bob = User.objects.create_user("bob", "bob@example.org", "bob-pw-1")
        self.eve = User.objects.create_user("bob@example.org", "eve@example.org", "eve-pw-1")

    def failed(self, user):
        return [(row["address"], row["user_agent"])
                for row in (_signin_row(record, user)
                            for record in member_auth_records(user)
                            .filter(action__in=("AUTH.LOGIN_FAILED", "AUTH.LOCKED")))]


class WhoseFailedSignInTests(TypedNameCase):
    def test_a_name_that_is_one_accounts_username_is_that_accounts_alone(self):
        # Eve's own wrong password: tried at Eve's account, and none of Bob's
        # business — Bob saw Eve's address and browser for it.
        fail("bob@example.org", address="203.0.113.66", browser="EveBrowser/2.0")
        self.assertEqual(self.failed(self.eve), [("203.0.113.66", "EveBrowser/2.0")])
        self.assertEqual(self.failed(self.bob), [])

    def test_each_record_belongs_to_one_account(self):
        fail("bob@example.org")
        record = member_auth_records(self.eve).get(action="AUTH.LOGIN_FAILED")
        self.assertEqual(record.object_id, str(self.eve.pk))
        self.assertEqual(record.object_description, "bob@example.org")   # what was typed
        self.assertFalse(member_auth_records(self.bob).filter(pk=record.pk).exists())

    def test_the_data_export_follows_the_same_rule(self):
        fail("bob@example.org")
        self.assertFalse(records_about(self.bob).filter(action="AUTH.LOGIN_FAILED").exists())
        self.assertTrue(records_about(self.eve).filter(action="AUTH.LOGIN_FAILED").exists())

    def test_an_address_no_account_signs_in_with_is_its_owners(self):
        # What the privacy notice promises: failed sign-ins with your username
        # or your e-mail address. Eve's address is nobody's username.
        fail("EVE@example.org")
        self.assertEqual(len(self.failed(self.eve)), 1)
        self.assertEqual(self.failed(self.bob), [])

    def test_a_username_in_another_case_is_its_accounts_unless_another_has_it_exactly(self):
        fail("BOB")
        self.assertEqual(len(self.failed(self.bob)), 1)
        rob = User.objects.create_user("Rob", "rob@example.org", "pw")
        User.objects.create_user("rob", "rob2@example.org", "pw")
        fail("Rob")                                   # Rob's exactly, never rob's
        self.assertEqual(len(self.failed(rob)), 1)
        self.assertEqual(len(self.failed(User.objects.get(username="rob"))), 0)
        fail("ROB")                                   # two accounts answer: nobody's
        self.assertEqual(len(self.failed(rob)), 1)

    def test_a_name_nobody_has_is_nobodys(self):
        fail("mallory")
        for user in (self.bob, self.eve):
            self.assertEqual(self.failed(user), [])

    def test_a_pause_is_the_account_its_name_was_tried_at(self):
        identity.on_signin_locked(scope="account_address", address="198.51.100.9",
                                  failures=10, minutes=15, username="bob@example.org")
        self.assertEqual(member_auth_records(self.eve).filter(action="AUTH.LOCKED").count(), 1)
        self.assertFalse(member_auth_records(self.bob).filter(action="AUTH.LOCKED").exists())
        # A whole address paused names nobody.
        identity.on_signin_locked(scope="address", address="198.51.100.9",
                                  failures=50, minutes=15)
        self.assertEqual(member_auth_records(self.eve).filter(action="AUTH.LOCKED").count(), 1)


class ApprovalMailTests(TestCase):
    """The mail an applicant gets when a referrer accepts them."""

    def setUp(self):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        self.guild = Community.objects.create(name="Cedar Guild", slug="cedar")
        self.applicant = User.objects.create(username="cedar_newbie", email="newbie@example.com",
                                             is_active=False)
        application = MembershipApplication.objects.create(
            email="newbie@example.com", community=self.guild, code="424242",
            expires_at=timezone.now() + timedelta(days=7))
        self.referrer = Person.objects.create(
            user=User.objects.create_user("voucher", "voucher@example.com", "pw"),
            display_name="Voucher")
        self.referrer.communities.add(self.guild)
        self.ref = ReferenceRequest.objects.create(application=application,
                                                   referrer=self.referrer)

    def accept(self):
        self.client.force_login(self.referrer.user)
        self.client.post(reverse("socialhub:reference_accept", args=[self.ref.pk]))
        self.assertEqual(len(mail.outbox), 1)
        return mail.outbox[0]

    def test_it_names_the_username_to_sign_in_with_not_the_address(self):
        sent = self.accept()
        self.assertEqual(sent.to, ["newbie@example.com"])
        self.assertRegex(sent.body, r"\bcedar_newbie\b")
        self.assertNotIn("newbie@example.com", sent.body)
        self.assertIsNone(re.search(r"e-?mail address", sent.body, re.IGNORECASE), sent.body)

    def test_a_member_with_the_same_address_is_not_the_one_named(self):
        # The address found two accounts and the mail was never sent.
        User.objects.create_user("elder", "newbie@example.com", "pw")
        sent = self.accept()
        self.assertRegex(sent.body, r"\bcedar_newbie\b")
        self.assertNotRegex(sent.body, r"\belder\b")


class NoUsernameIsAnotherAccountsAddressTests(TestCase):
    """And the squat itself: a username that is another account's e-mail
    address is refused at the application, and an address that is another
    account's username at the e-mail change — whoever types the address at
    the sign-in would be trying the other account. One's own address stays
    a username one may choose."""

    def setUp(self):
        from toto.socialhub.models import PrivacyNotice

        cache.clear()
        self.addCleanup(cache.clear)
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        PrivacyNotice.objects.create(version=1, text_pl="Informacja", text_en="Notice")
        self.guild = Community.objects.create(name="Cedar Guild", slug="cedar")
        self.bob = User.objects.create_user("bob", "bob@example.org", "Bob-pw-2026")

    def apply(self, username, email):
        return self.client.post(reverse("socialhub:membership_application"), {
            "username": username, "email": email, "community": self.guild.pk,
            "privacy_version": 1, "privacy_accept": "on"})

    def test_an_applicant_cannot_take_a_members_address_as_username(self):
        response = self.apply("Bob@Example.org", "eve@example.org")
        self.assertEqual(response.status_code, 200)
        self.assertIn("username", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username__iexact="bob@example.org").exists())

    def test_an_applicant_may_sign_in_with_their_own_address(self):
        response = self.apply("eve@example.org", "eve@example.org")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="eve@example.org").exists())

    def test_a_member_cannot_move_to_an_address_that_is_another_accounts_username(self):
        User.objects.create_user("carol@example.org", "carol@elsewhere.example", "pw")
        self.client.force_login(self.bob)
        response = self.client.post(reverse("account:email"), {
            "new_email": "Carol@example.org", "password": "Bob-pw-2026"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("new_email", response.context["email_form"].errors)
        self.assertEqual(mail.outbox, [])
