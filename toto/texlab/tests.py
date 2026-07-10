"""Tests for the file-based LaTeX workspace (index/create) and vault wiring."""

from __future__ import annotations

import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.texlab.views import BLANK_TEX_DOCUMENT
from toto.vault.models import Bucket, VaultDirectory, VaultFile
from toto.vault.plugins import VaultEditorPlugin
from toto.vault.views import CreateEmptyFileView

User = get_user_model()


class WorkspaceTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._tmp)
        self._override.enable()
        self.addCleanup(self._override.disable)

        Platform.objects.create(
            site_name="Toto", author="Test", publication_year=2026, active=True
        )

        self.alice = User.objects.create_user("alice", password="pass")
        self.bob = User.objects.create_user("bob", password="pass")
        self.bucket = Bucket.objects.create(name="Lab", slug="lab", owner=self.alice)
        self.directory = VaultDirectory.objects.create(
            name="Papers", bucket=self.bucket, owner=self.alice
        )

    def _make_tex(self, owner=None, is_public=False, title="paper.tex", file_type="latex"):
        owner = owner or self.alice
        return VaultFile.objects.create(
            owner=owner,
            title=title,
            file_type=file_type,
            is_public=is_public,
            bucket=self.bucket,
            directory=self.directory,
            file=SimpleUploadedFile(title, BLANK_TEX_DOCUMENT.encode("utf-8")),
        )

    # ── Vault wiring ────────────────────────────────────────────────

    def test_editor_plugin_registered_for_latex(self):
        editor = VaultEditorPlugin.for_file_type("latex")
        self.assertIsNotNone(editor)
        vf = self._make_tex()
        self.assertEqual(
            editor.get_editor_url(vf), reverse("texlab:file_display", args=[vf.pk])
        )

    def test_vault_blank_tex_mirrors_texlab_literal(self):
        # The vault "New file" seed must stay in sync with the workspace's.
        self.assertEqual(CreateEmptyFileView._INITIAL["latex"], BLANK_TEX_DOCUMENT)

    # ── Workspace index ─────────────────────────────────────────────

    def test_index_lists_own_and_public_documents(self):
        self._make_tex(title="mine.tex")
        self._make_tex(owner=self.bob, is_public=True, title="shared.tex")
        self._make_tex(owner=self.bob, is_public=False, title="hidden.tex")
        self._make_tex(is_public=True, title="refs.bib", file_type="bib")

        self.client.force_login(self.alice)
        res = self.client.get(reverse("texlab:index"))
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        self.assertIn("mine.tex", body)
        self.assertIn("shared.tex", body)
        self.assertNotIn("hidden.tex", body)
        self.assertIn("refs.bib", body)

    def test_index_anonymous_sees_public_only(self):
        self._make_tex(title="mine.tex")
        self._make_tex(is_public=True, title="shared.tex")
        res = self.client.get(reverse("texlab:index"))
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        self.assertNotIn("mine.tex", body)
        self.assertIn("shared.tex", body)

    def test_index_links_play_for_tex_only(self):
        vf_tex = self._make_tex(is_public=True, title="talk.tex")
        vf_bib = self._make_tex(is_public=True, title="refs.bib", file_type="bib")
        res = self.client.get(reverse("texlab:index"))
        body = res.content.decode()
        self.assertIn(reverse("texplay:latex_play", args=[vf_tex.pk]), body)
        self.assertNotIn(reverse("texplay:latex_play", args=[vf_bib.pk]), body)

    # ── Create ──────────────────────────────────────────────────────

    def test_create_new_document_redirects_to_editor(self):
        self.client.force_login(self.alice)
        res = self.client.post(reverse("texlab:create"))
        self.assertEqual(res.status_code, 302)
        vf = VaultFile.objects.filter(owner=self.alice, file_type="latex").latest("pk")
        self.assertEqual(res.url, reverse("texlab:file_display", args=[vf.pk]))
        # Created in the user's personal bucket with the blank skeleton.
        self.assertEqual(vf.bucket.slug, f"personal-{self.alice.username}")
        self.assertTrue(vf.title.endswith(".tex"))
        with vf.file.open("r") as f:
            content = f.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        self.assertEqual(content, BLANK_TEX_DOCUMENT)

    def test_create_generates_unique_keys(self):
        self.client.force_login(self.alice)
        self.client.post(reverse("texlab:create"))
        self.client.post(reverse("texlab:create"))
        keys = set(
            VaultFile.objects.filter(
                owner=self.alice, bucket__slug=f"personal-{self.alice.username}"
            ).values_list("key", flat=True)
        )
        self.assertEqual(len(keys), 2)

    def test_create_requires_login(self):
        res = self.client.post(reverse("texlab:create"))
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.url)
