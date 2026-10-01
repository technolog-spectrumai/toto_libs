"""A host's own privacy notice (2026-10-01, 37c.16): ``PRIVACY_NOTICE_TEXTS``
names two plain-text files; the ingress publishes them as version 1 on a
fresh database and as the next version over the placeholder, never over
somebody's text, and names what they still leave to fill in."""

from __future__ import annotations

import io
import shutil
import tempfile
from pathlib import Path

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform

from .models import PrivacyNotice
from .privacy import (PLACEHOLDER_EN, PLACEHOLDER_PL, host_texts, is_placeholder, markers,
                      publish, seed_notice, seed_placeholder)

HOST_PL = """Informacja o prywatności serwisu testowego.

1. Administrator danych

Administratorem jest [[UZUPEŁNIJ: nazwa administratora]]. Kontakt: [[UZUPEŁNIJ: adres e-mail]].
"""

HOST_EN = """The test service's privacy notice.

1. The controller

The controller is [[FILL IN: the controller's name]]. Contact: [[FILL IN: e-mail address]].
"""


def _published_records():
    if not apps.is_installed("toto.audit"):
        return None
    from toto.audit.models import AuditRecord

    return list(AuditRecord.objects.filter(action="PRIVACY.NOTICE_PUBLISHED").order_by("sequence"))


class HostTextsFixture(TestCase):
    """Two files in a folder of their own, named the way a host names them."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Zen", author="T", publication_year=2026, active=True)

    def setUp(self):
        folder = Path(tempfile.mkdtemp(prefix="privacy-notice-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        self.pl, self.en = folder / "notice_pl.txt", folder / "notice_en.txt"
        self.pl.write_text(HOST_PL, encoding="utf-8")
        self.en.write_text(HOST_EN.replace("\n", "\r\n"), encoding="utf-8")
        self.texts = {"pl": str(self.pl), "en": str(self.en)}

    def host(self):
        return override_settings(PRIVACY_NOTICE_TEXTS=self.texts)

    def without_texts(self):
        return override_settings(PRIVACY_NOTICE_TEXTS=None)


class SeedTests(HostTextsFixture):
    def test_a_fresh_database_gets_the_hosts_text_as_version_one(self):
        with self.host():
            notice = seed_notice()
        self.assertEqual(notice.version, 1)
        self.assertEqual(notice.text_pl, HOST_PL.strip())
        self.assertEqual(notice.text_en, HOST_EN.strip())       # \r\n made \n, as publish stores
        self.assertIsNone(notice.published_by)
        self.assertFalse(is_placeholder(notice))

    def test_a_platform_on_the_placeholder_gets_the_hosts_text_next(self):
        with self.without_texts():
            first = seed_notice()
        self.assertTrue(is_placeholder(first))
        with self.host():
            second = seed_notice()
        self.assertEqual(second.version, 2)
        self.assertEqual(PrivacyNotice.current(), second)
        self.assertEqual(second.text_en, HOST_EN.strip())
        first.refresh_from_db()                               # never an edit
        self.assertEqual((first.text_pl, first.text_en), (PLACEHOLDER_PL.strip(), PLACEHOLDER_EN.strip()))
        records = _published_records()
        if records is not None:
            self.assertEqual([(r.metadata["version"], r.metadata["previous"]) for r in records],
                             [(1, None), (2, 1)])
        # What an applicant accepted under version 1 stays readable at its address.
        page = self.client.get(reverse("socialhub:privacy_notice_version", args=[1]), {"lang": "en"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "PLACEHOLDER")
        current = self.client.get(reverse("socialhub:privacy_notice"), {"lang": "en"})
        self.assertContains(current, "The test service")
        self.assertNotContains(current, "PLACEHOLDER")

    def test_a_placeholder_left_in_one_language_is_still_the_placeholder(self):
        publish(text_pl=PLACEHOLDER_PL, text_en="Our own English text.")
        with self.host():
            notice = seed_notice()
        self.assertEqual(notice.version, 2)
        self.assertEqual(notice.text_pl, HOST_PL.strip())

    def test_somebodys_text_is_never_replaced(self):
        publish(text_pl="Nasza", text_en="Ours")
        with self.host():
            self.assertIsNone(seed_notice())
        self.assertEqual(PrivacyNotice.objects.count(), 1)
        self.assertEqual(PrivacyNotice.current().text_en, "Ours")

    def test_seeding_again_publishes_nothing(self):
        with self.host():
            seed_notice()
            self.assertIsNone(seed_notice())
        self.assertEqual(PrivacyNotice.objects.count(), 1)

    def test_a_host_without_texts_still_gets_the_placeholder(self):
        for unset in (None, {}):
            with self.subTest(setting=unset), override_settings(PRIVACY_NOTICE_TEXTS=unset):
                PrivacyNotice.objects.all().delete()
                self.assertIsNone(host_texts())
                notice = seed_notice()
                self.assertEqual(notice.version, 1)
                self.assertTrue(is_placeholder(notice))
                self.assertIsNone(seed_notice())        # and stays on it

    def test_a_named_text_that_cannot_be_read_is_said_plainly(self):
        self.en.unlink()
        with self.host(), self.assertRaisesMessage(ImproperlyConfigured, "PRIVACY_NOTICE_TEXTS['en']"):
            seed_notice()
        with override_settings(PRIVACY_NOTICE_TEXTS={"pl": str(self.pl)}), \
                self.assertRaisesMessage(ImproperlyConfigured, "names no 'en' text"):
            seed_notice()
        self.assertFalse(PrivacyNotice.objects.exists())

    def test_the_old_name_seeds_the_same(self):
        self.assertIs(seed_placeholder, seed_notice)
        with self.host():
            self.assertEqual(seed_placeholder().text_pl, HOST_PL.strip())


class MarkerTests(HostTextsFixture):
    def test_markers_are_named_once_in_the_order_they_come(self):
        text = ("[[FILL IN: a name]] and [[FILL IN: an\n   address]], again [[FILL IN: a name]]; "
                "[single brackets] are no marker.")
        self.assertEqual(markers(text), ["FILL IN: a name", "FILL IN: an address"])
        self.assertEqual(markers(PLACEHOLDER_EN) + markers(PLACEHOLDER_PL), [])
        self.assertEqual(markers(""), [])


class IngressTests(HostTextsFixture):
    def ingress(self):
        out = io.StringIO()
        call_command("ingress_socialhub", mode="realistic", stdout=out)
        return out.getvalue()

    def test_the_ingress_publishes_the_hosts_text_and_names_what_is_left(self):
        with self.host():
            said = self.ingress()
        self.assertEqual(PrivacyNotice.current().text_pl, HOST_PL.strip())
        self.assertIn("Privacy notice v1 (the host's text) published.", said)
        self.assertIn("still leaves 4 place(s) to fill in", said)
        for line in ("pl: [[UZUPEŁNIJ: nazwa administratora]]", "pl: [[UZUPEŁNIJ: adres e-mail]]",
                     "en: [[FILL IN: the controller's name]]", "en: [[FILL IN: e-mail address]]"):
            self.assertIn(line, said)
        with self.host():
            again = self.ingress()                  # nothing seeded: nothing said
        self.assertNotIn("Privacy notice", again)
        self.assertEqual(PrivacyNotice.objects.count(), 1)

    def test_the_ingress_moves_a_placeholder_platform_to_the_hosts_text(self):
        with self.without_texts():
            said = self.ingress()
        self.assertIn("Privacy notice v1 (placeholder) published.", said)
        self.assertNotIn("to fill in", said)
        with self.host():
            said = self.ingress()
        self.assertIn("Privacy notice v2 (the host's text) published.", said)
        self.assertEqual(PrivacyNotice.current().version, 2)
        self.assertEqual(PrivacyNotice.current().text_en, HOST_EN.strip())

    def test_a_text_with_nothing_left_to_fill_in_is_published_quietly(self):
        self.pl.write_text("Pełna informacja.", encoding="utf-8")
        self.en.write_text("The full notice.", encoding="utf-8")
        with self.host():
            said = self.ingress()
        self.assertIn("Privacy notice v1 (the host's text) published.", said)
        self.assertNotIn("to fill in", said)
