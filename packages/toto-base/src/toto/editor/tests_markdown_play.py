"""The editor's Play button for a Markdown file (2026-10-02).

The toolbar asks the vault's Play registry — the reader is the host's (on
zenobia, toto.htmlview's GitHub-style page) — so a stub plugin stands in for
it here, and the button follows the registry: there with a plugin, gone
without one, never on a type that has none.

`toto` is a namespace package: run as `manage.py test toto.editor.tests_markdown_play`.
"""

from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import NoReverseMatch, reverse

from toto.editor.tests import EditorTestCase
from toto.vault.models import VaultFile
from toto.vault.plugins import VaultPlayPlugin


class _StubReader(VaultPlayPlugin):
    file_type = "markdown"

    def get_play_url(self, vault_file):
        return f"/read/{vault_file.pk}/"


class _Unmounted(VaultPlayPlugin):
    file_type = "markdown"

    def get_play_url(self, vault_file):
        raise NoReverseMatch("not mounted")


class MarkdownPlayButtonTests(EditorTestCase):
    def setUp(self):
        super().setUp()
        self.md = VaultFile.objects.create(
            owner=self.owner, title="README.md", file_type="markdown", bucket=self.bucket,
            file=SimpleUploadedFile("README.md", b"# Hi\n"))

    def page(self, name="editor:markdown_display", vault_file=None):
        return self.client.get(reverse(name, args=[(vault_file or self.md).pk]))

    def test_the_toolbar_plays_through_the_registry(self):
        with mock.patch.dict(VaultPlayPlugin.registry, {"markdown": _StubReader()}):
            response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["play_url"], f"/read/{self.md.pk}/")
        self.assertContains(response, f'href="/read/{self.md.pk}/"')
        self.assertContains(response, 'id="play-file"')
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, '<span class="sr-only">Play</span>')

    def test_no_reader_no_button(self):
        registry = {k: v for k, v in VaultPlayPlugin.registry.items() if k != "markdown"}
        with mock.patch.dict(VaultPlayPlugin.registry, registry, clear=True):
            response = self.page()
        self.assertEqual(response.context["play_url"], "")
        self.assertNotContains(response, 'id="play-file"')

    def test_an_unmounted_reader_is_no_button_rather_than_a_500(self):
        with mock.patch.dict(VaultPlayPlugin.registry, {"markdown": _Unmounted()}):
            response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="play-file"')

    def test_a_text_file_has_no_play(self):
        with mock.patch.dict(VaultPlayPlugin.registry, {"markdown": _StubReader()}):
            response = self.page("editor:text_display", self.file)
        self.assertNotContains(response, 'id="play-file"')
