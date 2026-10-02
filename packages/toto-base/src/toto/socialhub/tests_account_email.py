"""Your account, the e-mail section (2026-09-30; on the own profile's Account
tab since 2026-10-02, stage 50): a new address is asked for, a single-use
link is mailed to it, and only that member opening it signed in, within a
day, moves the account; the old address is told. Both steps are on the chain
with the addresses masked. The link is the one mailed before stage 50 too,
and lands on the Account tab.
"""

from __future__ import annotations

import re
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.audit.models import AuditRecord
from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub import email_change
from toto.socialhub.models import Community, MembershipApplication, PendingEmailChange

User = get_user_model()
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
OLD = "ada@example.test"
NEW = "ada.new@example.org"


@override_settings(EMAIL_BACKEND=LOCMEM)
class EmailTestCase(TestCase):
    def setUp(self):
        from django.core.cache import cache

        # The request limits count in the cache, which no test rolls back.
        cache.clear()
        self.addCleanup(cache.clear)
        Platform.objects.create(site_name="Test", author="Tests",
                                publication_year=2026, active=True)
        self.user = User.objects.create_user("ada", OLD, "Correct-horse-9")
        self.person = Person.objects.create(user=self.user, display_name="Ada", email=OLD)
        self.profile_url = reverse("socialhub:profile_details", args=[self.person.slug])
        #: Where the e-mail doors go back to: the Account tab, at the section.
        self.back = self.profile_url + "?tab=account#email"
        self.client.force_login(self.user)

    def page(self, tab="account", client=None):
        """The own page on ``tab``, by the way the header goes (/account/)."""
        return (client or self.client).get(f"{reverse('account:home')}?tab={tab}", follow=True)

    def ask(self, address=NEW, password="Correct-horse-9"):
        return self.client.post(reverse("account:email"),
                                {"new_email": address, "password": password})

    def link(self):
        """The link in the last mail, as a path with its query."""
        found = re.search(r"https?://\S+", mail.outbox[-1].body)
        self.assertIsNotNone(found)
        url = urlparse(found.group(0))
        return f"{url.path}?{url.query}"

    def token(self):
        return parse_qs(urlparse(self.link()).query)["token"][0]

    def address(self):
        self.user.refresh_from_db()
        return self.user.email


class RequestTests(EmailTestCase):
    def test_the_section_posts_to_its_own_door(self):
        self.assertEqual(reverse("account:email"), "/account/email/")
        response = self.page()
        self.assertContains(response, 'id="email"')
        self.assertContains(response, 'action="/account/email/"')
        self.assertContains(response, 'name="new_email"')

    def test_the_get_is_refused(self):
        self.assertEqual(self.client.get(reverse("account:email")).status_code, 405)

    def test_the_link_goes_to_the_new_address_only(self):
        response = self.ask()
        self.assertRedirects(response, self.back, fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [NEW])
        self.assertEqual(mail.outbox[0].extra_headers["X-Toto-Notice"], "email_change_confirm")
        self.assertTrue(self.link().startswith("/account/email/confirm/?token="))

    def test_the_address_is_not_changed_before_the_click(self):
        self.ask()
        self.assertEqual(self.address(), OLD)
        self.person.refresh_from_db()
        self.assertEqual(self.person.email, OLD)
        row = PendingEmailChange.objects.get(user=self.user)
        self.assertEqual(row.new_email, NEW)
        # Only the token's hash is kept.
        self.assertNotIn(self.token(), row.token_hash)
        self.assertContains(self.page(), NEW)

    def test_the_request_is_on_the_chain_masked(self):
        self.ask()
        record = AuditRecord.objects.get(action="AUTH.EMAIL_CHANGE_REQUESTED")
        self.assertEqual(record.metadata, {"new_email": "a***@example.org"})
        self.assertEqual(record.actor_user_id, self.user.pk)
        self.assertNotIn(NEW, str(record.metadata) + str(record.request_source))

    def test_an_address_of_another_account_is_refused(self):
        User.objects.create_user("bob", "Taken@Example.org", "x")
        response = self.ask("taken@example.org")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(PendingEmailChange.objects.exists())

    def test_an_address_of_another_person_is_refused(self):
        Person.objects.create(display_name="Imported", email="contact@example.org")
        self.assertEqual(self.ask("contact@example.org").status_code, 400)

    def test_an_address_of_an_applicant_is_refused(self):
        community = Community.objects.create(name="Club", slug="club")
        MembershipApplication.objects.create(email="applicant@example.org",
                                             community=community,
                                             expires_at=timezone.now() + timedelta(days=1))
        self.assertEqual(self.ask("applicant@example.org").status_code, 400)

    def test_the_current_address_is_refused(self):
        self.assertEqual(self.ask(OLD.upper()).status_code, 400)
        self.assertEqual(mail.outbox, [])

    def test_a_malformed_address_is_refused(self):
        self.assertEqual(self.ask("not an address").status_code, 400)

    def test_a_federated_account_is_sent_to_its_provider(self):
        self.user.set_unusable_password()
        self.user.save()
        self.client.force_login(self.user)
        self.ask()
        self.assertEqual(mail.outbox, [])
        self.assertFalse(PendingEmailChange.objects.exists())

    def test_a_mail_that_cannot_leave_is_said(self):
        with mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError):
            response = self.ask()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.address(), OLD)


class ConfirmTests(EmailTestCase):
    def test_the_link_applies_once(self):
        self.ask()
        link = self.link()
        # The mailed link, as mailed: the same door it was before stage 50,
        # landing on the Account tab.
        self.assertTrue(link.startswith("/account/email/confirm/?token="))
        response = self.client.get(link)
        self.assertRedirects(response, self.back, fetch_redirect_response=False)
        self.assertEqual(self.address(), NEW)
        self.person.refresh_from_db()
        self.assertEqual(self.person.email, NEW)
        self.assertFalse(PendingEmailChange.objects.exists())
        # Again: refused, and nothing moves.
        self.user.email = "later@example.org"
        self.user.save(update_fields=["email"])
        self.client.get(link)
        self.assertEqual(self.address(), "later@example.org")
        self.assertEqual(AuditRecord.objects.filter(action="AUTH.EMAIL_CHANGED").count(), 1)

    def test_both_notices_are_sent(self):
        self.ask()
        self.client.get(self.link())
        self.assertEqual(len(mail.outbox), 2)
        told = mail.outbox[1]
        self.assertEqual(told.to, [OLD])
        self.assertEqual(told.extra_headers["X-Toto-Notice"], "email_changed")
        self.assertIn("a***@example.org", told.body)
        self.assertNotIn(NEW, told.body)

    def test_the_change_is_on_the_chain_masked(self):
        self.ask()
        token = self.token()
        self.client.get(self.link())
        record = AuditRecord.objects.get(action="AUTH.EMAIL_CHANGED")
        self.assertEqual(record.metadata, {"old_email": "a***@example.test",
                                           "new_email": "a***@example.org",
                                           "sessions_ended": 0})
        # The token rides in the query string, which the chain does not keep.
        self.assertEqual(record.request_source["path"], "/account/email/confirm/")
        self.assertNotIn(token, str(record.request_source))

    def test_an_expired_link_is_refused(self):
        self.ask()
        PendingEmailChange.objects.update(
            created=timezone.now() - timedelta(hours=email_change.LINK_HOURS, minutes=1))
        self.client.get(self.link())
        self.assertEqual(self.address(), OLD)
        self.assertFalse(PendingEmailChange.objects.exists())
        self.assertEqual(len(mail.outbox), 1)

    def test_another_member_opening_the_link_is_refused(self):
        self.ask()
        link = self.link()
        bob = User.objects.create_user("bob", "bob@example.test", "x")
        self.client.force_login(bob)
        self.client.get(link)
        bob.refresh_from_db()
        self.assertEqual(bob.email, "bob@example.test")
        self.assertEqual(self.address(), OLD)
        # Not spent: the member it was sent for can still use it.
        self.client.force_login(self.user)
        self.client.get(link)
        self.assertEqual(self.address(), NEW)

    def test_signed_out_the_link_asks_to_sign_in(self):
        self.ask()
        link = self.link()
        self.client.logout()
        response = self.client.get(link)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.address(), OLD)
        self.assertTrue(PendingEmailChange.objects.exists())

    def test_a_made_up_token_is_refused(self):
        self.ask()
        self.client.get(reverse("account:email_confirm") + "?token=nope")
        self.client.get(reverse("account:email_confirm"))
        self.assertEqual(self.address(), OLD)

    def test_a_newer_request_replaces_the_older_link(self):
        self.ask()
        first = self.link()
        self.ask("ada.third@example.org")
        self.client.get(first)
        self.assertEqual(self.address(), OLD)
        self.client.get(self.link())
        self.assertEqual(self.address(), "ada.third@example.org")

    def test_an_address_taken_meanwhile_is_refused_at_the_click(self):
        self.ask()
        User.objects.create_user("bob", NEW, "x")
        self.client.get(self.link())
        self.assertEqual(self.address(), OLD)
        self.assertFalse(PendingEmailChange.objects.exists())

    def test_a_person_with_another_address_keeps_it(self):
        self.person.email = "desk@example.test"
        self.person.save(update_fields=["email"])
        self.ask()
        self.client.get(self.link())
        self.person.refresh_from_db()
        self.assertEqual(self.person.email, "desk@example.test")
        self.assertEqual(self.address(), NEW)


class ReviewTests(EmailTestCase):
    """Review 2026-10-01: asking had no limit, a confirmed change left the
    member's other sessions signed in, and the link ignored the address the
    account had when it was asked for."""

    def test_asking_is_limited_per_member(self):
        for n in range(email_change.REQUESTS_PER_MEMBER):
            self.ask(f"ada{n}@example.org")
        sent = len(mail.outbox)
        response = self.ask("ada.more@example.org")
        self.assertRedirects(response, self.back, fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), sent)
        self.assertNotEqual(PendingEmailChange.objects.get(user=self.user).new_email,
                            "ada.more@example.org")

    def test_refused_forms_count_too(self):
        for _ in range(email_change.REQUESTS_PER_MEMBER):
            self.ask("not an address")
        self.ask()
        self.assertEqual(mail.outbox, [])

    def test_one_address_gets_a_few_links_a_day_whoever_asks(self):
        for n in range(email_change.MAILS_PER_ADDRESS):
            member = User.objects.create_user(f"m{n}", f"m{n}@example.test", "x")
            self.client.force_login(member)
            self.ask("victim@example.org", password="x")
        self.assertEqual(len(mail.outbox), email_change.MAILS_PER_ADDRESS)
        self.client.force_login(self.user)
        self.ask("victim@example.org")
        self.assertEqual(len(mail.outbox), email_change.MAILS_PER_ADDRESS)
        self.assertFalse(PendingEmailChange.objects.filter(user=self.user).exists())

    def test_a_confirmed_change_ends_the_other_sessions(self):
        from django.test import Client

        other = Client()
        other.force_login(self.user)
        self.assertEqual(other.get(self.profile_url).status_code, 200)
        self.ask()
        self.client.get(self.link())
        self.assertEqual(self.address(), NEW)
        self.assertEqual(other.get(self.profile_url).status_code, 302)
        self.assertEqual(self.client.get(self.profile_url).status_code, 200)
        record = AuditRecord.objects.get(action="AUTH.EMAIL_CHANGED")
        self.assertEqual(record.metadata["sessions_ended"], 1)

    def test_the_link_is_bound_to_the_old_address(self):
        self.ask()
        link = self.link()
        self.user.email = "moved.back@example.test"
        self.user.save(update_fields=["email"])
        self.client.get(link)
        self.assertEqual(self.address(), "moved.back@example.test")
        self.assertFalse(PendingEmailChange.objects.exists())
        self.assertFalse(AuditRecord.objects.filter(action="AUTH.EMAIL_CHANGED").exists())


class PasswordTests(EmailTestCase):
    """Review 2026-10-01: a session alone could move the account's address
    (and with it every password reset) away from its owner."""

    def test_the_current_password_is_asked_for(self):
        self.assertContains(self.page(), 'name="password"')
        for wrong in ("", "not-my-password"):
            with self.subTest(wrong=wrong):
                self.assertEqual(self.ask(password=wrong).status_code, 400)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(PendingEmailChange.objects.exists())

    @override_settings(
        AUTHENTICATION_BACKENDS=["toto.core.signin_lockout.SigninLockoutBackend",
                                 "django.contrib.auth.backends.ModelBackend"],
        LOGIN_DELAY_AFTER=0, LOGIN_LOCK_AFTER=3, LOGIN_ADDRESS_LOCK_AFTER=0)
    def test_wrong_passwords_count_toward_the_lockout(self):
        for _ in range(3):
            self.ask(password="not-my-password")
        response = self.ask()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mail.outbox, [])


class LabelTests(EmailTestCase):
    def test_the_sign_ins_list_names_both_steps(self):
        self.ask()
        self.client.get(self.link())
        page = self.page("security").content.decode()
        self.assertIn("New e-mail address asked for", page)
        self.assertIn("E-mail address changed", page)
        self.assertNotIn("AUTH.EMAIL_CHANGE", page)


class MaskTests(TestCase):
    def test_mask(self):
        self.assertEqual(email_change.mask_email("jane@example.org"), "j***@example.org")
        self.assertEqual(email_change.mask_email("broken"), "***")
        self.assertEqual(email_change.mask_email(""), "***")
