"""How OCR is reached: from Office, and from a file you already have.

Two doors, because people arrive two ways — with a file in hand, and with one
already stored. The vault's wand covers the second.
"""

from __future__ import annotations

import io
import tempfile

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core import office
from toto.core.models import Platform
from toto.vault.models import Bucket, VaultFile
from toto.vault.plugins import FileServicePlugin

MEDIA = tempfile.mkdtemp(prefix="ocr-office-")


def make_file(owner, bucket, title, file_type, data=b"x"):
    vault_file = VaultFile(owner=owner, title=title, key=title.replace(".", "-"),
                           bucket=bucket, file_type=file_type)
    vault_file.file.save(title, ContentFile(data), save=True)
    return vault_file


@override_settings(MEDIA_ROOT=MEDIA)
class OfficeToolTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Test", author="t",
                                publication_year=2026, active=True)
        cls.user = User.objects.create_user("member", password="x")

    def setUp(self):
        self.client.force_login(self.user)

    def test_office_offers_the_tool_when_ocr_is_installed(self):
        tools = {t["slug"]: t for t in office.available_tools()}
        if django_apps.is_installed("toto.ocr"):
            self.assertIn("ocr", tools)
            self.assertEqual(tools["ocr"]["url"], reverse("ocr:home"))
        else:
            self.assertNotIn("ocr", tools)

    def test_a_tool_is_not_a_tab(self):
        """A Section is defined by the file types it lists. A tool lists none,
        and forcing one into that grammar would make the next tool's claim to a
        tab unanswerable."""
        self.assertNotIn("ocr", {s.slug for s in office.SECTIONS})

    def test_the_office_page_renders_the_link(self):
        # office:index redirects to the first tab this host serves.
        response = self.client.get(reverse("office:index"), follow=True)
        self.assertEqual(response.status_code, 200)
        if django_apps.is_installed("toto.ocr"):
            self.assertContains(response, reverse("ocr:home"))

    def test_the_tool_page_is_not_mounted_under_office(self):
        """The subscription gate reads the entitlement from the URL namespace,
        and Office's is deliberately free and GET-only — so a POST-accepting
        page under /office/ would be a way to create paid content for nothing.
        """
        self.assertTrue(reverse("ocr:submit").startswith("/ocr/"))


@override_settings(MEDIA_ROOT=MEDIA)
class WandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("member2", password="x")
        cls.bucket = Bucket.objects.create(name="mine", owner=cls.user)

    def plugin(self):
        return FileServicePlugin.registry.get("ocr")

    def test_it_offers_itself_on_images_and_pdfs(self):
        for file_type in ("image", "pdf"):
            with self.subTest(file_type=file_type):
                vault_file = make_file(self.user, self.bucket,
                                       f"a-{file_type}.bin", file_type)
                keys = {p.key for p in FileServicePlugin.for_file(vault_file)}
                self.assertIn("ocr", keys)

    def test_it_stays_out_of_the_way_on_everything_else(self):
        vault_file = make_file(self.user, self.bucket, "clip.mp4", "video")
        self.assertNotIn("ocr",
                         {p.key for p in FileServicePlugin.for_file(vault_file)})

    def test_it_never_offers_itself_on_an_encrypted_file(self):
        vault_file = make_file(self.user, self.bucket, "secret.pdf", "pdf")
        VaultFile.objects.filter(pk=vault_file.pk).update(is_encrypted=True)
        vault_file.refresh_from_db()
        self.assertFalse(self.plugin().accepts(vault_file))

    def test_it_is_a_builder_so_it_works_without_the_media_wheel(self):
        """A builder plugin redirects to its own page and never touches the
        FileServiceRun substrate, which lives in a wheel most hosts decline."""
        self.assertTrue(self.plugin().builder)

    def test_the_builder_url_carries_the_file(self):
        vault_file = make_file(self.user, self.bucket, "scan.pdf", "pdf")
        url = self.plugin().builder_url(vault_file)
        self.assertIn("/ocr/builder/", url)
        self.assertIn(f"file={vault_file.pk}", url)

    def test_the_builder_path_is_one_nginx_raises_the_body_limit_for(self):
        """deploy.py writes `location ~ ^/ocr/(submit|builder)/`. Renaming
        either route without changing that regex would silently restore the
        10 MB server-wide cap, and a scanned book would 413."""
        for name in ("ocr:submit", "ocr:builder"):
            path = reverse(name)
            self.assertTrue(path.startswith("/ocr/submit/")
                            or path.startswith("/ocr/builder/"), path)
