"""How OCR is reached: from the Tools hub, and from a file you already have.

Two doors, because people arrive two ways — with a file in hand, and with one
already stored. The vault's wand covers the second. (This module was
tests_office.py while the hub was Office's strip; Office retired to limbo on
2026-09-02 and the tools kept their room.)
"""

from __future__ import annotations

import io
import tempfile

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core import tools as tools_hub
from toto.core.models import Platform
from toto.vault.models import Bucket, VaultFile
from toto.vault.plugins import FileServicePlugin

MEDIA = tempfile.mkdtemp(prefix="ocr-tools-")


def make_file(owner, bucket, title, file_type, data=b"x"):
    vault_file = VaultFile(owner=owner, title=title, key=title.replace(".", "-"),
                           bucket=bucket, file_type=file_type)
    vault_file.file.save(title, ContentFile(data), save=True)
    return vault_file


@override_settings(MEDIA_ROOT=MEDIA)
class ToolsHubTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Test", author="t",
                                publication_year=2026, active=True)
        cls.user = User.objects.create_user("member", password="x")

    def setUp(self):
        self.client.force_login(self.user)

    def test_the_hub_offers_the_tool_when_ocr_is_installed(self):
        tools = {t["slug"]: t for t in tools_hub.available_tools()}
        if django_apps.is_installed("toto.ocr"):
            self.assertIn("ocr", tools)
            self.assertEqual(tools["ocr"]["url"], reverse("ocr:home"))
        else:
            self.assertNotIn("ocr", tools)

    def test_the_tool_is_in_the_tools_strip(self):
        """A tool lists no vault file types — it has no rows, no folder panel
        and no New button; what it has is a tab in the TOOLS strip. (The
        not-an-Office-section half of this assertion retired with Office:
        there is no Section table left to stay out of.)"""
        if django_apps.is_installed("toto.ocr"):
            self.assertIn("ocr", {t["slug"] for t in tools_hub.tools_tabs()})

    def test_the_tool_page_shows_the_tools_strip_with_its_own_tab_lit(self):
        """Clicking a tab must not drop you out of the tabbed interface.

        The active tab is passed as a slug, because this page lives in
        another app and there is no hub URL for the strip to compare the
        request path against.
        """
        if not django_apps.is_installed("toto.ocr"):
            self.skipTest("toto.ocr is not installed on this host")
        response = self.client.get(reverse("ocr:home"))
        self.assertEqual(response.status_code, 200)
        tabs = response.context["sections"]
        self.assertEqual([t["slug"] for t in tabs if t["active"]], ["ocr"])
        # And the OTHER TOOLS are really there to click.
        for tool in tools_hub.available_tools():
            self.assertContains(response, tool["url"])

    def test_the_hub_renders_the_link(self):
        """The one page whose job is offering the tool's door."""
        response = self.client.get(reverse("tools:index"))
        self.assertEqual(response.status_code, 200)
        if django_apps.is_installed("toto.ocr"):
            self.assertContains(response, reverse("ocr:home"))

    def test_the_tool_page_is_not_mounted_under_the_hub(self):
        """The subscription gate reads the entitlement from the URL namespace,
        and the hub's is deliberately free and GET-only — so a POST-accepting
        page under /tools/ would be a way to use paid tools for nothing.
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
