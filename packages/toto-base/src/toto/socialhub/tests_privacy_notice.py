"""The privacy notice (2026-10-01, RODO): versions, the public pages, who
may publish, the audit record, the escaping and the ingress seed."""

from __future__ import annotations

import io

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform

from .models import PrivacyNotice
from .privacy import (MAX_TEXT, PLACEHOLDER_EN, PLACEHOLDER_MARK_EN, PLACEHOLDER_MARK_PL,
                      PLACEHOLDER_PL, publish, seed_placeholder)

User = get_user_model()


def _audit_records(action):
    if not apps.is_installed("toto.audit"):
        return None
    from toto.audit.models import AuditRecord

    return list(AuditRecord.objects.filter(action=action).order_by("sequence"))


class PrivacyFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Zen", author="T", publication_year=2026, active=True)
        cls.member = User.objects.create_user("pn-member", password="x")
        cls.staff = User.objects.create_user("pn-staff", password="x", is_staff=True)
        cls.root = User.objects.create_superuser("pn-root", "root@example.org", "x")
        if apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=io.StringIO())   # root → the Superuser plan
        cls.root = User.objects.get(pk=cls.root.pk)
        # Made after the bootstrap: a superuser who never took the plan.
        cls.bare = User.objects.create_superuser("pn-bare", "bare@example.org", "x")


class VersionTests(PrivacyFixture):
    def test_publishing_adds_the_next_version_and_keeps_the_old_one(self):
        first = publish(text_pl="Pierwsza", text_en="First", user=self.root)
        second = publish(text_pl="Druga", text_en="Second", user=self.root)
        self.assertEqual((first.version, second.version), (1, 2))
        self.assertEqual(PrivacyNotice.current(), second)
        first.refresh_from_db()
        self.assertEqual((first.text_pl, first.text_en), ("Pierwsza", "First"))
        self.assertEqual(second.published_by, self.root)

    def test_the_text_is_in_the_readers_language_or_the_other(self):
        notice = publish(text_pl="Po polsku", text_en="In English")
        self.assertEqual(notice.text_for("pl"), "Po polsku")
        self.assertEqual(notice.text_for("pl-pl"), "Po polsku")
        self.assertEqual(notice.text_for("en"), "In English")
        self.assertEqual(notice.text_for("de"), "In English")
        notice.text_en = ""
        self.assertEqual(notice.text_for("en"), "Po polsku")

    def test_a_text_may_be_a_hundred_thousand_characters_and_no_more(self):
        # A guard against a pasted book (2026-10-01, 37c.16): a real notice
        # is about 50,000, which the old cap of exactly that refused.
        self.assertEqual(MAX_TEXT, 100_000)
        notice = publish(text_pl="ą" * MAX_TEXT, text_en="a" * 50_001)
        self.assertEqual((len(notice.text_pl), len(notice.text_en)), (MAX_TEXT, 50_001))
        with self.assertRaises(ValueError):
            publish(text_pl="Krótka", text_en="a" * (MAX_TEXT + 1))
        self.assertEqual(PrivacyNotice.objects.count(), 1)

    def test_a_version_needs_both_languages(self):
        with self.assertRaises(ValueError):
            publish(text_pl="  ", text_en="Only English")
        self.assertFalse(PrivacyNotice.objects.exists())

    def test_publishing_is_audited_without_the_text(self):
        publish(text_pl="Pierwsza", text_en="First")
        notice = publish(text_pl="Druga wersja", text_en="Second version", user=self.root)
        records = _audit_records("PRIVACY.NOTICE_PUBLISHED")
        if records is None:
            self.skipTest("toto.audit is not installed")
        self.assertEqual(len(records), 2)
        last = records[-1]
        self.assertEqual(last.object_id, str(notice.pk))
        self.assertEqual(last.metadata["version"], 2)
        self.assertEqual(last.metadata["previous"], 1)
        self.assertEqual(last.metadata["length_en"], len("Second version"))
        self.assertNotIn("Second version", repr(last.metadata) + last.object_description)

    # A host that names no texts of its own (PRIVACY_NOTICE_TEXTS, 37c.16):
    # zenobia names its own, so these say so. tests_privacy_seed has the rest.
    @override_settings(PRIVACY_NOTICE_TEXTS=None)
    def test_the_placeholder_is_seeded_once_and_marked_in_both_languages(self):
        notice = seed_placeholder()
        self.assertEqual(notice.version, 1)
        self.assertEqual(notice.text_en.splitlines()[0], PLACEHOLDER_MARK_EN)
        self.assertEqual(notice.text_pl.splitlines()[0], PLACEHOLDER_MARK_PL)
        self.assertTrue(PLACEHOLDER_MARK_EN.startswith("PLACEHOLDER — replace with your organisation's"))
        self.assertIsNone(seed_placeholder())
        self.assertEqual(PrivacyNotice.objects.count(), 1)

    @override_settings(PRIVACY_NOTICE_TEXTS=None)
    def test_the_ingress_seeds_version_one_in_realistic_and_full(self):
        for mode in ("realistic", "full"):
            with self.subTest(mode=mode):
                PrivacyNotice.objects.all().delete()
                call_command("ingress_socialhub", mode=mode, stdout=io.StringIO())
                notice = PrivacyNotice.current()
                self.assertEqual(notice.version, 1)
                self.assertEqual(notice.text_pl, PLACEHOLDER_PL.strip())
                self.assertEqual(notice.text_en, PLACEHOLDER_EN.strip())

    def test_the_ingress_never_replaces_a_published_version(self):
        publish(text_pl="Nasza", text_en="Ours")
        call_command("ingress_socialhub", mode="realistic", stdout=io.StringIO())
        self.assertEqual(PrivacyNotice.objects.count(), 1)
        self.assertEqual(PrivacyNotice.current().text_en, "Ours")


class PublicPageTests(PrivacyFixture):
    def test_signed_out_the_notice_and_each_version_are_readable(self):
        publish(text_pl="Stara", text_en="The old one")
        publish(text_pl="Nowa", text_en="The new one")
        response = self.client.get(reverse("socialhub:privacy_notice"), HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The new one")
        old = self.client.get(reverse("socialhub:privacy_notice_version", args=[1]),
                              HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(old.status_code, 200)
        self.assertContains(old, "The old one")
        self.assertContains(old, reverse("socialhub:privacy_notice"))

    def test_a_version_that_does_not_exist_is_404(self):
        publish(text_pl="Jedna", text_en="One")
        self.assertEqual(self.client.get(reverse("socialhub:privacy_notice_version", args=[7])).status_code,
                         404)

    def test_nothing_published_is_a_page_saying_so(self):
        response = self.client.get(reverse("socialhub:privacy_notice"))
        self.assertEqual(response.status_code, 200)

    def test_the_language_follows_the_reader_and_can_be_switched(self):
        publish(text_pl="Treść po polsku", text_en="English text")
        url = reverse("socialhub:privacy_notice")
        self.assertContains(self.client.get(url, {"lang": "pl"}), "Treść po polsku")
        self.assertContains(self.client.get(url, {"lang": "en"}), "English text")
        self.assertNotContains(self.client.get(url, {"lang": "en"}), "Treść po polsku")

    def test_the_text_is_escaped_never_html(self):
        publish(text_pl="x", text_en=('<script>alert(1)</script> <img src=x onerror=alert(2)>\n\n'
                                      'javascript:alert(3) https://example.org/rodo'))
        body = self.client.get(reverse("socialhub:privacy_notice"), {"lang": "en"}).content.decode()
        self.assertNotIn("<script>alert", body)
        self.assertNotIn("<img src=x", body)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)
        self.assertNotIn('href="javascript:', body)
        # A paragraph per blank line and an address as a link: the only markup.
        self.assertIn('<a href="https://example.org/rodo"', body)
        self.assertIn("<p>", body)

    def test_the_welcome_page_links_the_notice(self):
        body = self.client.get(reverse("core:welcome")).content.decode()
        self.assertIn('data-testid="welcome-privacy-notice"', body)
        self.assertIn(f'href="{reverse("socialhub:privacy_notice")}"', body)

    def test_the_footer_links_the_notice(self):
        body = self.client.get(reverse("socialhub:privacy_notice")).content.decode()
        self.assertIn(f'href="{reverse("socialhub:privacy_notice")}"', body)


class EditorTests(PrivacyFixture):
    def url(self):
        return reverse("socialhub:privacy_notice_edit")

    def test_signed_out_the_editor_goes_to_the_login(self):
        response = self.client.post(self.url(), {"text_pl": "a", "text_en": "b"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])
        self.assertFalse(PrivacyNotice.objects.exists())

    def test_only_a_superuser_on_the_plan_may_publish(self):
        visitors = [("member", self.member), ("staff", self.staff)]
        if apps.is_installed("toto.subscriptions"):
            visitors.append(("superuser without the plan", self.bare))
        for label, user in visitors:
            self.client.force_login(user)
            with self.subTest(visitor=label):
                self.assertEqual(self.client.get(self.url()).status_code, 403)
                self.assertEqual(self.client.post(self.url(), {"text_pl": "a", "text_en": "b"}).status_code,
                                 403)
        self.assertFalse(PrivacyNotice.objects.exists())
        self.assertEqual(_audit_records("PRIVACY.NOTICE_PUBLISHED") or [], [])

    def test_a_superuser_on_the_plan_publishes_a_new_version(self):
        publish(text_pl="Pierwsza", text_en="First")
        self.client.force_login(self.root)
        page = self.client.get(self.url())
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Pierwsza")          # the form starts from the current text
        response = self.client.post(self.url(), {"text_pl": "Druga\r\n", "text_en": " Second "})
        self.assertRedirects(response, reverse("socialhub:privacy_notice_version", args=[2]),
                             fetch_redirect_response=False)
        notice = PrivacyNotice.current()
        self.assertEqual((notice.version, notice.text_pl, notice.text_en), (2, "Druga", "Second"))
        self.assertEqual(notice.published_by, self.root)
        self.assertEqual(PrivacyNotice.objects.get(version=1).text_en, "First")

    def test_the_editor_takes_a_text_longer_than_the_old_cap(self):
        publish(text_pl="Pierwsza", text_en="First")
        self.client.force_login(self.root)
        self.assertContains(self.client.get(self.url()), f'maxlength="{MAX_TEXT}"')
        long_pl = "Długi tekst. " * 4_000             # 52,000 characters
        response = self.client.post(self.url(), {"text_pl": long_pl, "text_en": "Second"})
        self.assertRedirects(response, reverse("socialhub:privacy_notice_version", args=[2]),
                             fetch_redirect_response=False)
        self.assertEqual(PrivacyNotice.current().text_pl, long_pl.strip())
        too_long = {"text_pl": "x" * (MAX_TEXT + 1), "text_en": "Third"}
        self.assertRedirects(self.client.post(self.url(), too_long), self.url(),
                             fetch_redirect_response=False)
        self.assertEqual(PrivacyNotice.objects.count(), 2)

    def test_a_refusal_publishes_nothing_and_keeps_what_was_typed(self):
        publish(text_pl="Pierwsza", text_en="First")
        self.client.force_login(self.root)
        for data in ({"text_pl": "Tylko polski", "text_en": ""},
                     {"text_pl": "Pierwsza", "text_en": "First"}):
            with self.subTest(data=data):
                response = self.client.post(self.url(), data)
                self.assertRedirects(response, self.url(), fetch_redirect_response=False)
                self.assertEqual(PrivacyNotice.objects.count(), 1)
        self.client.post(self.url(), {"text_pl": "Szkic <b>", "text_en": ""})
        self.assertContains(self.client.get(self.url()), "Szkic &lt;b&gt;")

    def test_the_public_page_offers_the_editor_only_to_a_publisher(self):
        publish(text_pl="Pierwsza", text_en="First")
        page = reverse("socialhub:privacy_notice")
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get(page), self.url())
        self.client.force_login(self.root)
        self.assertContains(self.client.get(page), self.url())
