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

    def test_latex_and_bib_route_to_generic_editor(self):
        # Editing .tex/.bib now happens in the generic ACE editor (toto.editor),
        # not the bespoke texlab editor.
        latex = VaultEditorPlugin.for_file_type("latex")
        bib = VaultEditorPlugin.for_file_type("bib")
        self.assertIsNotNone(latex)
        self.assertIsNotNone(bib)
        vf = self._make_tex()
        self.assertEqual(latex.get_editor_url(vf), reverse("editor:latex_display", args=[vf.pk]))
        vfb = self._make_tex(title="refs.bib", file_type="bib")
        self.assertEqual(bib.get_editor_url(vfb), reverse("editor:bib_display", args=[vfb.pk]))

    def test_ensure_compile_workflow_is_idempotent(self):
        from toto.texlab.workflow import (
            COMPILE_TASK_NAME, COMPILE_WORKFLOW_SLUG, ensure_compile_workflow,
        )
        from toto.workflows.models import WorkflowNode

        wf1 = ensure_compile_workflow()
        wf2 = ensure_compile_workflow()
        self.assertEqual(wf1.pk, wf2.pk)
        self.assertEqual(wf1.slug, COMPILE_WORKFLOW_SLUG)
        nodes = WorkflowNode.objects.filter(
            workflow=wf1, node_type=WorkflowNode.PREDEFINED_TASK, task_name=COMPILE_TASK_NAME,
        )
        self.assertEqual(nodes.count(), 1)  # not duplicated on re-seed

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

    # ── Create ──────────────────────────────────────────────────────

    def test_create_new_document_redirects_to_editor(self):
        self.client.force_login(self.alice)
        res = self.client.post(reverse("texlab:create"))
        self.assertEqual(res.status_code, 302)
        vf = VaultFile.objects.filter(owner=self.alice, file_type="latex").latest("pk")
        self.assertEqual(res.url, reverse("editor:latex_display", args=[vf.pk]))
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

    def test_create_into_chosen_bucket_and_directory(self):
        self.client.force_login(self.alice)
        res = self.client.post(reverse("texlab:create"), data={
            "filename": "My Report",
            "bucket_id": str(self.bucket.pk),
            "directory_id": str(self.directory.pk),
        })
        self.assertEqual(res.status_code, 302)
        vf = VaultFile.objects.filter(owner=self.alice, file_type="latex").latest("pk")
        self.assertEqual(res.url, reverse("editor:latex_display", args=[vf.pk]))
        self.assertEqual(vf.bucket, self.bucket)
        self.assertEqual(vf.directory, self.directory)
        # A ".tex" suffix isn't duplicated when the user types it.
        self.assertEqual(vf.title, "My Report.tex")
        self.assertEqual(vf.key, "my-report")

    def test_create_rejects_foreign_bucket(self):
        # Bob cannot create a file inside Alice's bucket.
        self.client.force_login(self.bob)
        res = self.client.post(reverse("texlab:create"), data={
            "filename": "sneaky",
            "bucket_id": str(self.bucket.pk),
        })
        self.assertEqual(res.status_code, 404)
        self.assertFalse(VaultFile.objects.filter(owner=self.bob).exists())

    def test_index_shows_location_path(self):
        # The card surfaces where the file lives: "<bucket> / <folder path>".
        self._make_tex(title="paper.tex")
        self.client.force_login(self.alice)
        res = self.client.get(reverse("texlab:index"))
        self.assertContains(res, "Lab / Papers")

    def test_index_renders_create_modal_with_picker(self):
        self.client.force_login(self.alice)
        res = self.client.get(reverse("texlab:index"))
        body = res.content.decode()
        # Modal fields + picker data payload are present for authenticated users.
        self.assertIn("window._newFileBuckets", body)
        self.assertIn('name="filename"', body)
        self.assertIn('name="bucket_id"', body)
        self.assertIn('name="directory_id"', body)
        # The user's own bucket is offered in the picker JSON.
        self.assertIn("Lab", body)

    # ── Compile (workflow over Celery/Redis) ────────────────────────

    def test_editor_latex_page_has_compile_button(self):
        vf = self._make_tex()
        self.client.force_login(self.alice)
        res = self.client.get(reverse("editor:latex_display", args=[vf.pk]))
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        self.assertIn('id="compile-file"', body)
        self.assertIn(reverse("texlab:compile_latex", args=[vf.pk]), body)
        self.assertIn(reverse("texlab:compile_status", args=[0]), body)

    def test_editor_bib_page_has_no_compile_button(self):
        vf = self._make_tex(title="refs.bib", file_type="bib")
        self.client.force_login(self.alice)
        res = self.client.get(reverse("editor:bib_display", args=[vf.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('id="compile-file"', res.content.decode())

    def test_compile_dispatches_the_dedicated_workflow(self):
        from unittest.mock import patch
        from toto.texlab.workflow import COMPILE_WORKFLOW_SLUG
        from toto.workflows.models import WorkflowRun

        vf = self._make_tex()
        self.client.force_login(self.alice)
        with patch("toto.texlab.views.celery_available", return_value=True), \
             patch("toto.workflows.tasks.start_workflow_run_task.delay") as m_delay:
            res = self.client.post(reverse("texlab:compile_latex", args=[vf.pk]))

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "queued")
        # A WorkflowRun on the dedicated workflow carries the compile input…
        wf_run = WorkflowRun.objects.get(pk=data["workflow_run_id"])
        self.assertEqual(wf_run.workflow.slug, COMPILE_WORKFLOW_SLUG)
        self.assertEqual(
            wf_run.input_data,
            {"data": {"vault_file_pk": vf.pk, "run_id": data["run_id"]}},
        )
        # …the CompileRun is linked to it, and the run went to the Celery broker.
        from toto.texlab.models import CompileRun
        run = CompileRun.objects.get(id=data["run_id"])
        self.assertEqual(run.workflow_run_id, wf_run.pk)
        m_delay.assert_called_once_with(wf_run.pk)

    def test_compile_without_pdflatex_reports_actionable_error(self):
        # A host without the TeX toolchain must produce a clear log, not the raw
        # "[Errno 2] No such file or directory: 'pdflatex'".
        from unittest.mock import patch

        vf = self._make_tex()
        self.client.force_login(self.alice)
        with patch("toto.texlab.views.celery_available", return_value=False), \
             patch("toto.texlab.compile.subprocess.run", side_effect=FileNotFoundError(2, "No such file or directory", "pdflatex")):
            res = self.client.post(reverse("texlab:compile_latex", args=[vf.pk]))
        self.assertEqual(res.status_code, 500)
        data = res.json()
        self.assertEqual(data["status"], "failed")
        self.assertIn("pdflatex is not installed", data["log"])
        self.assertIn("INSTALL_TEXLIVE=1", data["log"])

    def test_ingress_texlab_seeds_workflow_idempotently(self):
        from django.core.management import call_command
        from toto.texlab.workflow import COMPILE_WORKFLOW_SLUG
        from toto.workflows.models import Workflow

        call_command("ingress_texlab")
        call_command("ingress_texlab", full=True)  # ingress_all passes full=
        wfs = Workflow.objects.filter(slug=COMPILE_WORKFLOW_SLUG)
        self.assertEqual(wfs.count(), 1)
        self.assertEqual(wfs.first().nodes.count(), 1)

    def test_texlab_in_ingress_allowed_apps(self):
        from django.conf import settings
        self.assertIn("toto.texlab", settings.INGRESS_ALLOWED_APPS)
