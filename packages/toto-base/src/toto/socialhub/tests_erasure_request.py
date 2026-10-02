"""*Erase my account* (2026-10-01, RODO): a member files a request on their
profile's Your data tab (stage 50; My account until then), a superuser on the
plan sees it with the console command and may
decline it, and only the console's ``erase_user`` carries it out — closing
the request, which outlives the account by its username snapshot.

Nothing on the web erases; a member never sees another member's request.
"""

from __future__ import annotations

import io
import json

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.socialhub import erasure
from toto.socialhub.models import ErasureRequest

User = get_user_model()


def _actions(prefix="PRIVACY.ERASURE"):
    if not apps.is_installed("toto.audit"):
        return None
    from toto.audit.models import AuditRecord

    return list(AuditRecord.objects.filter(action__startswith=prefix).order_by("sequence"))


class ErasureFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Zen", author="T", publication_year=2026, active=True)
        cls.ada = User.objects.create_user("er-ada", "ada@example.test", "x")
        cls.bob = User.objects.create_user("er-bob", "bob@example.test", "x")
        cls.root = User.objects.create_superuser("er-root", "root@example.test", "x")
        if apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=io.StringIO())   # root → the Superuser plan
        cls.root = User.objects.get(pk=cls.root.pk)
        # Made after the bootstrap: a superuser who never took the plan.
        cls.bare = User.objects.create_superuser("er-bare", "bare@example.test", "x")

    def file(self, **data):
        return self.client.post(reverse("account:erasure_request"), {"confirm": "yes", **data})

    #: The Your data tab; these members have no profile, so it is drawn at
    #: /account/ itself.
    DATA_TAB = "/account/?tab=data"

    def page(self):
        return self.client.get(self.DATA_TAB, follow=True)

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


class FilingTests(ErasureFixture):
    def test_the_member_files_one_request_of_their_own(self):
        self.client.force_login(self.ada)
        response = self.file(user=self.bob.pk)
        self.assertRedirects(response, self.DATA_TAB + "#erasure",
                             fetch_redirect_response=False)
        ticket = ErasureRequest.objects.get()
        self.assertEqual((ticket.user, ticket.username, ticket.status),
                         (self.ada, "er-ada", ErasureRequest.OPEN))
        self.assertTrue(User.objects.filter(pk=self.ada.pk, is_active=True).exists())
        records = _actions()
        if records is not None:
            self.assertEqual([r.action for r in records], ["PRIVACY.ERASURE_REQUESTED"])
            self.assertEqual(records[0].actor_user, self.ada)

    def test_without_the_confirmation_nothing_is_filed(self):
        self.client.force_login(self.ada)
        response = self.client.post(reverse("account:erasure_request"), {})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ErasureRequest.objects.exists())

    def test_a_second_open_request_is_refused(self):
        self.client.force_login(self.ada)
        self.file()
        response = self.file()
        self.assertEqual(ErasureRequest.objects.count(), 1)
        self.assertTrue(any("already" in m for m in self.messages(response)))
        with self.assertRaises(IntegrityError), transaction.atomic():
            ErasureRequest.objects.create(user=self.ada, username="er-ada")

    def test_a_get_or_a_stranger_files_nothing(self):
        self.client.force_login(self.ada)
        self.assertEqual(self.client.get(reverse("account:erasure_request")).status_code, 405)
        self.client.logout()
        response = self.file()
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])
        self.assertFalse(ErasureRequest.objects.exists())

    def test_the_page_offers_the_confirmation_and_then_shows_the_request(self):
        self.client.force_login(self.ada)
        page = self.page().content.decode()
        self.assertIn('data-testid="erasure-confirm"', page)
        self.assertIn("audit trail", page)
        self.file()
        page = self.page().content.decode()
        self.assertIn('data-testid="erasure-latest"', page)
        self.assertNotIn('data-testid="erasure-confirm"', page)

    def test_a_member_sees_only_their_own_request(self):
        erasure.decline(erasure.file_request(self.bob), by=self.root, note="Bob's secret reason")
        self.client.force_login(self.ada)
        page = self.page().content.decode()
        self.assertNotIn('data-testid="erasure-latest"', page)
        self.assertNotIn("Bob&#x27;s secret reason", page)
        self.assertNotIn("Bob's secret reason", page)
        self.assertEqual(self.client.get(reverse("socialhub:erasure_requests")).status_code, 403)


class ListTests(ErasureFixture):
    def test_only_a_superuser_on_the_plan_sees_the_list(self):
        erasure.file_request(self.ada)
        visitors = [("member", self.ada)]
        if apps.is_installed("toto.subscriptions"):
            visitors.append(("superuser without the plan", self.bare))
        for label, user in visitors:
            self.client.force_login(user)
            with self.subTest(visitor=label):
                self.assertEqual(self.client.get(reverse("socialhub:erasure_requests")).status_code, 403)

    @override_settings(SOCIALHUB_ERASURE_COMMAND="python3 tools/delete_user.py {username}")
    def test_the_list_shows_each_open_request_with_its_command(self):
        erasure.file_request(self.ada)
        self.client.force_login(self.root)
        page = self.client.get(reverse("socialhub:erasure_requests"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "python3 tools/delete_user.py er-ada")
        self.assertContains(page, reverse("socialhub:erasure_request_decline",
                                          args=[ErasureRequest.objects.get().pk]))

    def test_the_command_quotes_the_username(self):
        ticket = ErasureRequest(username="a b;rm")
        with self.settings(SOCIALHUB_ERASURE_COMMAND=""):
            self.assertEqual(erasure.console_command(ticket), "python manage.py erase_user 'a b;rm'")
        with self.settings(SOCIALHUB_ERASURE_COMMAND="erase {username}"):
            self.assertEqual(erasure.console_command(ticket), "erase 'a b;rm'")

    def test_a_superuser_declines_with_a_note_the_member_reads(self):
        ticket = erasure.file_request(self.ada)
        self.client.force_login(self.root)
        url = reverse("socialhub:erasure_request_decline", args=[ticket.pk])
        self.client.post(url, {"note": "  "})
        ticket.refresh_from_db()
        self.assertTrue(ticket.is_open)
        self.client.post(url, {"note": "You hold the treasury keys."})
        ticket.refresh_from_db()
        self.assertEqual((ticket.status, ticket.handled_by, ticket.note),
                         (ErasureRequest.DECLINED, self.root, "You hold the treasury keys."))
        self.assertTrue(User.objects.filter(pk=self.ada.pk).exists())
        records = _actions("PRIVACY.ERASURE_DECLINED")
        if records is not None:
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].actor_user, self.root)
            self.assertNotIn("treasury", json.dumps(records[0].metadata))
        self.client.force_login(self.ada)
        self.assertContains(self.page(), "You hold the treasury keys.")
        # Declined, it may be asked again.
        self.file()
        self.assertEqual(ErasureRequest.objects.filter(status=ErasureRequest.OPEN).count(), 1)

    def test_a_declined_request_names_the_members_remedies(self):
        """Whatever the note says (2026-10-01, 37c.21): a complaint to the
        President of UODO, or a court (RODO art. 12(4))."""
        ticket = erasure.file_request(self.ada)
        self.client.force_login(self.ada)
        self.assertNotContains(self.page(), "erasure-remedies")
        erasure.decline(ticket, by=self.root, note="You hold the treasury keys.")
        page = self.page()
        self.assertContains(page, "Prezes Urzędu Ochrony Danych Osobowych")
        self.assertContains(page, "take the matter to court")

    def test_the_dialog_says_what_the_erase_takes_and_what_stays(self):
        self.client.force_login(self.ada)
        page = self.page()
        for words in ("Also your profile picture", "pictures and voice recordings you sent",
                      "signed “Former member” instead of your name",
                      "Backups taken before the erase, until they age out"):
            self.assertContains(page, words)

    def test_a_member_cannot_decline(self):
        ticket = erasure.file_request(self.bob)
        self.client.force_login(self.ada)
        response = self.client.post(reverse("socialhub:erasure_request_decline", args=[ticket.pk]),
                                    {"note": "no"})
        self.assertEqual(response.status_code, 403)
        ticket.refresh_from_db()
        self.assertTrue(ticket.is_open)


class ConsoleTests(ErasureFixture):
    def erase(self, username, *extra):
        out = io.StringIO()
        call_command("erase_user", username, *extra, stdout=out)
        return json.loads(out.getvalue().strip().splitlines()[-1])

    def test_the_report_leaves_the_request_open(self):
        ticket = erasure.file_request(self.ada)
        self.erase("er-ada")
        ticket.refresh_from_db()
        self.assertTrue(ticket.is_open)

    def test_the_console_erase_closes_the_request_which_outlives_the_account(self):
        ticket = erasure.file_request(self.ada)
        other = erasure.file_request(self.bob)
        result = self.erase("er-ada", "--confirm", "er-ada")
        self.assertTrue(result["erased"])
        self.assertEqual(result["requests_closed"], [ticket.pk])
        self.assertFalse(User.objects.filter(username="er-ada").exists())
        ticket.refresh_from_db()
        self.assertEqual((ticket.user_id, ticket.username, ticket.status),
                         (None, "er-ada", ErasureRequest.DONE))
        self.assertIsNotNone(ticket.handled_at)
        other.refresh_from_db()
        self.assertTrue(other.is_open)
        records = _actions("PRIVACY.ERASURE_DONE")
        if records is not None:
            self.assertEqual([r.object_description for r in records], ["er-ada"])
        # The operators' list still shows it, by username.
        self.client.force_login(self.root)
        self.assertContains(self.client.get(reverse("socialhub:erasure_requests") + "?status=done"),
                            "er-ada")
