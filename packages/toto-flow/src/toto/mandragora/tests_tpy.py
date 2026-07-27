"""Tests for the .tpy file-backed notebook (XML format + vault-wired editor)."""

from __future__ import annotations

import json
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.mandragora import tpy_format
from toto.mandragora import tpy_views
from toto.vault.models import Bucket, VaultDirectory, VaultFile
from toto.vault.plugins import VaultEditorPlugin

User = get_user_model()


class TpyFormatTests(TestCase):
    def test_round_trip_preserves_everything(self):
        nb = tpy_format.TpyNotebook(
            title="Demo",
            dependencies=[
                tpy_format.TpyDependency("numpy", ">=1.21"),
                tpy_format.TpyDependency("pandas"),
            ],
            cells=[
                tpy_format.TpyCell(
                    cell_type="code",
                    source="import numpy as np\nprint(np.arange(3))",
                    execution_count=4,
                    stdout="[0 1 2]\n",
                    stderr="",
                    rich_output=[{"type": "execute_result", "data": {"text/plain": "3"}}],
                ),
                tpy_format.TpyCell(cell_type="markdown", source="# Title\n<b> & raw </b>"),
            ],
        )
        xml = tpy_format.dumps(nb)
        back = tpy_format.loads(xml)

        self.assertEqual(back.title, "Demo")
        self.assertEqual([d.pip_specifier() for d in back.dependencies], ["numpy>=1.21", "pandas"])
        self.assertEqual(len(back.cells), 2)
        self.assertEqual(back.cells[0].source, "import numpy as np\nprint(np.arange(3))")
        self.assertEqual(back.cells[0].execution_count, 4)
        self.assertEqual(back.cells[0].stdout, "[0 1 2]\n")
        self.assertEqual(back.cells[0].rich_output, [{"type": "execute_result", "data": {"text/plain": "3"}}])
        self.assertEqual(back.cells[1].cell_type, "markdown")
        self.assertEqual(back.cells[1].source, "# Title\n<b> & raw </b>")

    def test_idempotent(self):
        nb = tpy_format.new_notebook("Hello")
        nb.cells[0].source = "print('hi')"
        xml = tpy_format.dumps(nb)
        self.assertEqual(tpy_format.dumps(tpy_format.loads(xml)), xml)

    def test_control_chars_survive_via_base64(self):
        # ANSI-coloured tracebacks contain ESC (0x1b), illegal in XML 1.0.
        nb = tpy_format.new_notebook()
        nb.cells[0].stderr = "Traceback \x1b[31mERROR\x1b[0m\x00end"
        xml = tpy_format.dumps(nb)
        self.assertIn('enc="base64"', xml)
        back = tpy_format.loads(xml)
        self.assertEqual(back.cells[0].stderr, "Traceback \x1b[31mERROR\x1b[0m\x00end")

    def test_empty_and_blank_input_yields_default_notebook(self):
        for raw in ("", "   \n  "):
            nb = tpy_format.loads(raw)
            self.assertEqual(len(nb.cells), 1)
            self.assertEqual(nb.cells[0].cell_type, "code")

    def test_invalid_xml_raises(self):
        with self.assertRaises(tpy_format.TpyParseError):
            tpy_format.loads("<notebook><cells>")

    def test_wrong_root_raises(self):
        with self.assertRaises(tpy_format.TpyParseError):
            tpy_format.loads("<other></other>")

    def test_is_notebook_detects_root(self):
        self.assertTrue(tpy_format.is_notebook(tpy_format.dumps(tpy_format.new_notebook("x"))))
        self.assertTrue(tpy_format.is_notebook(b'<notebook version="1"></notebook>'))
        self.assertFalse(tpy_format.is_notebook("<signingDocument></signingDocument>"))
        self.assertFalse(tpy_format.is_notebook("<other/>"))
        self.assertFalse(tpy_format.is_notebook(""))

    def test_from_dict_sanitises(self):
        nb = tpy_format.TpyNotebook.from_dict({
            "title": "X",
            "dependencies": [{"package_name": " numpy ", "version_spec": " >=1 "}, {"package_name": ""}],
            "cells": [{"cell_type": "weird", "source": "x=1", "execution_count": "bad"}],
        })
        self.assertEqual(len(nb.dependencies), 1)
        self.assertEqual(nb.dependencies[0].package_name, "numpy")
        self.assertEqual(nb.dependencies[0].version_spec, ">=1")
        self.assertEqual(nb.cells[0].cell_type, "code")   # unknown type coerced
        self.assertEqual(nb.cells[0].execution_count, 0)  # bad int coerced


class TpyVaultIntegrationTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._tmp)
        self._override.enable()
        self.addCleanup(self._override.disable)

        # PageProcessor (used by the display view) requires an active platform.
        Platform.objects.create(site_name="Toto", author="Test", publication_year=2026, active=True)

        self.alice = User.objects.create_user("alice", password="pass")
        self.bob = User.objects.create_user("bob", password="pass")
        self.bucket = Bucket.objects.create(name="Lab", slug="lab", owner=self.alice)
        self.directory = VaultDirectory.objects.create(name="Notebooks", bucket=self.bucket, owner=self.alice)

    def _make_tpy(self, nb: tpy_format.TpyNotebook | None = None, owner=None) -> VaultFile:
        owner = owner or self.alice
        nb = nb or tpy_format.new_notebook("Analysis")
        vf = VaultFile.objects.create(
            owner=owner,
            title="analysis.tpy",
            file_type="notebook",
            bucket=self.bucket,
            directory=self.directory,
            file=SimpleUploadedFile("analysis.tpy", tpy_format.dumps(nb).encode("utf-8")),
        )
        return vf

    # ── extension / type detection ──────────────────────────────────────────
    def test_tpy_extension_retired(self):
        # The dedicated `.tpy`/notebook vault type is retired — notebooks are
        # ordinary .xml now, so `.tpy` no longer maps to a special type.
        self.assertNotEqual(VaultFile.detect_type("application/octet-stream", "x.tpy"), "notebook")

    # ── plugin wiring ─────────────────────────────────────────────────────────
    def test_editor_plugin_registered_for_notebook(self):
        plugin = VaultEditorPlugin.for_file_type("notebook")
        self.assertIsNotNone(plugin)
        vf = self._make_tpy()
        self.assertEqual(plugin.get_editor_url(vf), reverse("mandragora:tpy_display", args=[vf.pk]))

    # ── display ────────────────────────────────────────────────────────────────
    def test_display_loads_notebook_from_file(self):
        nb = tpy_format.TpyNotebook(
            title="Analysis",
            dependencies=[tpy_format.TpyDependency("numpy", ">=1.0")],
            cells=[tpy_format.TpyCell(cell_type="code", source="print('loaded from file')")],
        )
        vf = self._make_tpy(nb)
        self.client.force_login(self.alice)
        res = self.client.get(reverse("mandragora:tpy_display", args=[vf.pk]))
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        # notebook_json is embedded for client-side hydration as a real JSON object
        # (not a double-encoded string) so the front-end can JSON.parse it directly.
        self.assertIn('id="tpy-data"', body)
        start = body.index('id="tpy-data"')
        snippet = body[start:body.index("</script>", start)]
        payload = json.loads(snippet[snippet.index(">") + 1:])
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["cells"][0]["source"], "print('loaded from file')")
        self.assertEqual(payload["dependencies"][0]["package_name"], "numpy")
        # editor wiring is present
        self.assertIn(reverse("mandragora:tpy_save", args=[vf.pk]), body)
        self.assertIn(reverse("mandragora:tpy_run_cell", args=[vf.pk]), body)
        self.assertIn('id="cells"', body)
        self.assertIn('id="btn-save"', body)

    def test_display_denied_for_non_owner(self):
        vf = self._make_tpy()
        self.client.force_login(self.bob)
        res = self.client.get(reverse("mandragora:tpy_display", args=[vf.pk]))
        self.assertEqual(res.status_code, 404)

    # ── save ────────────────────────────────────────────────────────────────────
    def test_save_serialises_to_file(self):
        vf = self._make_tpy()
        self.client.force_login(self.alice)
        payload = {
            "title": "Analysis",
            "dependencies": [{"package_name": "scipy", "version_spec": "==1.11"}],
            "cells": [
                {"cell_type": "code", "source": "x = 2 + 2", "execution_count": 1,
                 "stdout": "4\n", "stderr": "", "rich_output": []},
                {"cell_type": "markdown", "source": "# Notes"},
            ],
        }
        res = self.client.post(
            reverse("mandragora:tpy_save", args=[vf.pk]),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")

        vf.refresh_from_db()
        with vf.file.open("r") as f:
            saved = f.read()
        if isinstance(saved, bytes):
            saved = saved.decode("utf-8")
        nb = tpy_format.loads(saved)
        self.assertEqual([d.pip_specifier() for d in nb.dependencies], ["scipy==1.11"])
        self.assertEqual(len(nb.cells), 2)
        self.assertEqual(nb.cells[0].source, "x = 2 + 2")
        self.assertEqual(nb.cells[0].stdout, "4\n")
        self.assertEqual(nb.cells[1].cell_type, "markdown")

    def test_save_denied_for_non_owner(self):
        vf = self._make_tpy()
        self.client.force_login(self.bob)
        res = self.client.post(
            reverse("mandragora:tpy_save", args=[vf.pk]),
            data=json.dumps({"cells": []}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 404)

    # ── in-app create (ordinary .xml) ─────────────────────────────────────────
    def test_create_makes_ordinary_xml_and_opens_editor(self):
        self.client.force_login(self.alice)
        res = self.client.post(reverse("mandragora:tpy_create"), data={
            "filename": "My Analysis", "title": "Analysis",
            "bucket_id": str(self.bucket.pk), "directory_id": str(self.directory.pk),
        })
        self.assertEqual(res.status_code, 302)
        vf = VaultFile.objects.filter(owner=self.alice, file_type="xml").latest("pk")
        self.assertEqual(res.url, reverse("mandragora:tpy_display", args=[vf.pk]))
        self.assertEqual(vf.file_type, "xml")
        self.assertTrue(vf.title.endswith(".xml"))
        self.assertEqual(vf.bucket, self.bucket)
        self.assertEqual(vf.directory, self.directory)
        with vf.file.open("r") as f:
            content = f.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        self.assertTrue(tpy_format.is_notebook(content))
        nb = tpy_format.loads(content)
        self.assertEqual(nb.title, "Analysis")
        self.assertEqual(len(nb.cells), 1)

    def test_create_requires_login(self):
        res = self.client.post(reverse("mandragora:tpy_create"), data={"filename": "x"})
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.url)

    # ── index / list ──────────────────────────────────────────────────────────
    def test_index_lists_notebooks_only_with_path(self):
        self._make_tpy()  # a real notebook (file_type="notebook")
        # a plain (non-notebook) xml file must be excluded
        VaultFile.objects.create(
            owner=self.alice, title="notes.xml", file_type="xml",
            bucket=self.bucket, directory=self.directory,
            file=SimpleUploadedFile("notes.xml", b"<notes><a/></notes>"),
        )
        self.client.force_login(self.alice)
        res = self.client.get(reverse("mandragora:tpy_index"))
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        self.assertIn("Analysis", body)       # the notebook's title
        self.assertNotIn("notes.xml", body)   # a plain xml file is excluded
        self.assertIn("Lab / Notebooks", body)

    # ── open gate ─────────────────────────────────────────────────────────────
    def test_display_404_on_non_notebook_xml(self):
        vf = VaultFile.objects.create(
            owner=self.alice, title="notes.xml", file_type="xml",
            bucket=self.bucket, file=SimpleUploadedFile("notes.xml", b"<notes/>"),
        )
        self.client.force_login(self.alice)
        self.assertEqual(
            self.client.get(reverse("mandragora:tpy_display", args=[vf.pk])).status_code, 404)

    def test_notebook_type_retired_from_vault_new_file(self):
        from toto.vault.views import CREATABLE_TYPES, CreateEmptyFileView
        self.assertNotIn("notebook", {t for t, _ in CREATABLE_TYPES})
        self.assertNotIn("notebook", CreateEmptyFileView._ALLOWED)
        self.assertNotIn("notebook", CreateEmptyFileView._INITIAL)


class TpyKernelTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._tmp)
        self._override.enable()
        self.addCleanup(self._override.disable)

        self.alice = User.objects.create_user("alice", password="pass")
        self.bucket = Bucket.objects.create(name="Lab", slug="lab", owner=self.alice)
        self.vf = VaultFile.objects.create(
            owner=self.alice,
            title="nb.tpy",
            file_type="notebook",
            bucket=self.bucket,
            file=SimpleUploadedFile("nb.tpy", tpy_format.dumps(tpy_format.new_notebook()).encode("utf-8")),
        )
        self.client.force_login(self.alice)

    def test_run_cell_returns_kernel_output(self):
        fake = {"stdout": "hello\n", "stderr": "", "execution_count": 1, "rich_output": []}
        with patch.object(tpy_views.client, "execute", return_value=fake) as m:
            res = self.client.post(
                reverse("mandragora:tpy_run_cell", args=[self.vf.pk]),
                data=json.dumps({"code": "print('hello')"}),
                content_type="application/json",
            )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["stdout"], "hello\n")
        self.assertEqual(data["execution_count"], 1)
        # session id is keyed by the vault file
        m.assert_called_once()
        self.assertEqual(m.call_args.args[0], f"tpy:{self.vf.pk}")

    def test_run_cell_reports_kernel_not_running(self):
        with patch.object(tpy_views.client, "execute", return_value={"error": "kernel_not_running"}):
            res = self.client.post(
                reverse("mandragora:tpy_run_cell", args=[self.vf.pk]),
                data=json.dumps({"code": "x"}),
                content_type="application/json",
            )
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["error"], "kernel_not_running")

    def test_start_kernel_passes_dependencies(self):
        with patch.object(tpy_views.client, "start", return_value={"state": "started"}) as m:
            res = self.client.post(
                reverse("mandragora:tpy_start_kernel", args=[self.vf.pk]),
                data=json.dumps({"dependencies": [{"package_name": "numpy", "version_spec": ">=1.21"}]}),
                content_type="application/json",
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["state"], "started")
        session_id, config = m.call_args.args[0], m.call_args.args[1]
        self.assertEqual(session_id, f"tpy:{self.vf.pk}")
        self.assertEqual(config["dependencies"], [{"package_name": "numpy", "version_spec": ">=1.21"}])

    def test_kernel_status(self):
        with patch.object(tpy_views.client, "status", return_value={"running": True}):
            res = self.client.get(reverse("mandragora:tpy_kernel_status", args=[self.vf.pk]))
        self.assertEqual(res.json()["status"], "running")
