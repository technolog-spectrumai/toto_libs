"""The Primula suite — run against ``toto.primula.testing.settings``.

Covers the whole sheet lifecycle (create → list → edit → save → versions → restore →
delete) against real vault storage, the version cap, the vault "open" routing, and the
ingress seeder. Sheet bytes live in a throwaway ``MEDIA_ROOT`` (see the settings).
"""

import json
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.vault.models import VaultFile
from toto.vault.plugins import VaultEditorPlugin

from . import sheet_format
from toto.vault import versions as vault_versions
from toto.vault.models import FileVersion
from .views import _read_raw


def make_sheet(owner, title="test-sheet", is_public=False, rows=None):
    """A ``sheet`` vault file seeded the way SheetCreateView seeds one."""
    workbook = (
        sheet_format.workbook_from_rows(title, rows)
        if rows is not None
        else sheet_format.new_workbook(title)
    )
    text = sheet_format.dumps(workbook)
    vf = VaultFile(
        owner=owner,
        title=f"{title}.json",
        key=title,
        file_type="sheet",
        is_public=is_public,
    )
    vf.file.save(f"{title}.json", ContentFile(text.encode("utf-8")), save=False)
    vf.content_hash = vf.create_hash()
    vf.save()
    # SheetCreateView cuts a named v1, and this helper claims to seed a sheet
    # the way that view does — so it has to do the same, or every test built on
    # it starts from a state the application can never actually produce.
    vault_versions.save_version(vf, author=owner, label="created")
    return vf


class SheetFormatTests(TestCase):
    def test_new_workbook_is_a_sheet(self):
        wb = sheet_format.new_workbook("Budget")
        self.assertTrue(sheet_format.is_sheet(sheet_format.dumps(wb)))
        self.assertEqual(wb["name"], "Budget")
        self.assertEqual(len(wb["sheets"]), 1)

    def test_is_sheet_rejects_other_json(self):
        self.assertFalse(sheet_format.is_sheet("{}"))
        self.assertFalse(sheet_format.is_sheet('{"rows": []}'))
        self.assertFalse(sheet_format.is_sheet("not json"))
        self.assertFalse(sheet_format.is_sheet(b"\xff\xfe"))

    def test_workbook_from_rows_types(self):
        wb = sheet_format.workbook_from_rows("t", [["Item", 3, True], ["", None, 1.5]])
        cells = wb["sheets"][wb["sheetOrder"][0]]["cellData"]
        self.assertEqual(cells["0"]["0"], {"v": "Item", "t": 1})
        self.assertEqual(cells["0"]["1"], {"v": 3, "t": 2})
        self.assertEqual(cells["0"]["2"], {"v": True, "t": 3})
        # Empty cells are skipped; the lone 1.5 lands at row 1 col 2.
        self.assertEqual(list(cells["1"].keys()), ["2"])


class SheetLifecycleTests(TestCase):
    def setUp(self):
        # PageProcessor 404s without an active Platform (ui/page.py:34).
        Platform.objects.create(site_name="Primula Test", author="Test", publication_year=2026)
        self.user = User.objects.create_user("ala", password="pw")
        self.other = User.objects.create_user("ola", password="pw")
        self.client.login(username="ala", password="pw")

    def test_create_makes_sheet_file_and_first_version(self):
        resp = self.client.post(reverse("primula:create"), {"filename": "budget"})
        vf = VaultFile.objects.get(file_type="sheet")
        self.assertRedirects(resp, reverse("primula:edit", args=[vf.pk]))
        self.assertEqual(vf.owner, self.user)
        self.assertEqual(vf.title, "budget.json")
        self.assertTrue(sheet_format.is_sheet(_read_raw(vf)))
        self.assertEqual(vf.versions.count(), 1)
        self.assertEqual(vf.versions.get().label, "created")

    def test_index_lists_own_and_public_only(self):
        mine = make_sheet(self.user, "mine")
        make_sheet(self.other, "theirs-public", is_public=True)
        make_sheet(self.other, "theirs-private")
        resp = self.client.get(reverse("primula:index"))
        self.assertEqual(resp.status_code, 200)
        titles = [s["title"] for s in resp.context["sheets"]]
        self.assertIn("mine.json", titles)
        self.assertIn("theirs-public.json", titles)
        self.assertNotIn("theirs-private.json", titles)
        row = next(s for s in resp.context["sheets"] if s["pk"] == mine.pk)
        self.assertTrue(row["is_owner"])

    def test_edit_renders_and_hydrates_snapshot(self):
        vf = make_sheet(self.user, "grid", rows=[["A", 1]])
        resp = self.client.get(reverse("primula:edit", args=[vf.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["can_edit"])
        self.assertEqual(
            resp.context["snapshot_json"]["sheets"]["sheet-1"]["cellData"]["0"]["0"]["v"],
            "A",
        )

    def test_the_editor_follows_the_platform_mode_and_background(self):
        # Univer is told the platform's dark mode at mount (its own dark theme
        # for the chrome and the grid's DEFAULT surfaces; explicit cell fills
        # come from the snapshot and stay as saved), and the workspace surface
        # is pinned to the SYSTEM background — read off <body> at runtime,
        # because the palette lives in the platform's theme record.
        vf = make_sheet(self.user, "grid")
        body = self.client.get(reverse("primula:edit", args=[vf.pk])).content.decode()
        self.assertIn("darkMode: dark", body)
        self.assertIn("--primula-system-bg", body)

    def test_edit_read_only_for_public_non_owner(self):
        vf = make_sheet(self.other, "shared", is_public=True)
        resp = self.client.get(reverse("primula:edit", args=[vf.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["can_edit"])

    def test_edit_private_of_other_user_404(self):
        vf = make_sheet(self.other, "secret")
        resp = self.client.get(reverse("primula:edit", args=[vf.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_save_rewrites_file_and_appends_version(self):
        vf = make_sheet(self.user, "grid")
        snap = sheet_format.workbook_from_rows("grid", [["saved", 42]])
        resp = self.client.post(
            reverse("primula:save", args=[vf.pk]),
            data=json.dumps(snap),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")
        vf.refresh_from_db()
        on_disk = json.loads(_read_raw(vf))
        self.assertEqual(on_disk["sheets"]["sheet-1"]["cellData"]["0"]["0"]["v"], "saved")
        self.assertEqual(vf.file_size_bytes, len(sheet_format.dumps(snap).encode("utf-8")))
        # Still 1: the creation version. A save writes the file, not history.
        self.assertEqual(vf.versions.count(), 1)

    def test_save_rejects_non_workbook_json(self):
        vf = make_sheet(self.user, "grid")
        resp = self.client.post(
            reverse("primula:save", args=[vf.pk]),
            data='{"just": "json"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post(
            reverse("primula:save", args=[vf.pk]),
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_save_denied_anonymous_and_non_owner(self):
        vf = make_sheet(self.user, "grid")
        snap = json.dumps(sheet_format.new_workbook("x"))
        self.client.logout()
        resp = self.client.post(
            reverse("primula:save", args=[vf.pk]), data=snap,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)
        self.client.login(username="ola", password="pw")
        resp = self.client.post(
            reverse("primula:save", args=[vf.pk]), data=snap,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_saving_no_longer_cuts_a_version_on_every_write(self):
        # This is the behaviour change: primula used to append a snapshot on
        # EVERY save and keep 50. That is an autosave log, not a history — it
        # buried the two saves anyone cared about under fifty they did not.
        # A version is now something a person asks for.
        vf = make_sheet(self.user, "grid")
        before = vf.versions.count()
        for i in range(5):
            snap = sheet_format.workbook_from_rows("grid", [[f"v{i}"]])
            self.client.post(
                reverse("primula:save", args=[vf.pk]),
                data=json.dumps(snap), content_type="application/json")

        self.assertEqual(vf.versions.count(), before)
        self.assertIn("v4", _read_raw(vf))          # the file itself still moved

    def test_a_new_sheet_gets_a_named_first_version(self):
        # Creating a sheet IS a deliberate act, so it earns a v1 to come back to.
        vf = make_sheet(self.user, "grid")
        self.assertEqual(vf.versions.count(), 1)
        self.assertEqual(vf.versions.first().number, 1)

    def test_a_version_is_cut_on_request_and_restores(self):
        from toto.vault import versions as vault_versions

        vf = make_sheet(self.user, "grid")
        original = _read_raw(vf)
        first = vf.versions.first()

        self.client.post(
            reverse("primula:save", args=[vf.pk]),
            data=json.dumps(sheet_format.workbook_from_rows("grid", [["later"]])),
            content_type="application/json")
        self.assertIn("later", _read_raw(vf))

        vault_versions.restore_version(first, actor=self.user)
        vf.refresh_from_db()

        self.assertEqual(_read_raw(vf), original)
        # Forward, never backward: the restore is itself the newest version.
        self.assertEqual(vf.versions.count(), 2)
        self.assertEqual(vf.versions.first().number, 2)

    def test_a_stale_save_is_refused_and_the_work_is_kept(self):
        # Primula had NO concurrency control at all — two tabs silently
        # overwrote each other. Now the loser is refused AND rescued.
        vf = make_sheet(self.user, "grid")
        snap = sheet_format.workbook_from_rows("grid", [["mine"]])
        snap["base_hash"] = "a-hash-from-before-somebody-else-saved"

        resp = self.client.post(
            reverse("primula:save", args=[vf.pk]),
            data=json.dumps(snap), content_type="application/json")

        self.assertEqual(resp.status_code, 409)
        self.assertNotIn("mine", _read_raw(vf))     # the file did not move
        rescued = vf.versions.filter(is_conflict=True).first()
        self.assertIsNotNone(rescued)               # but the work survived
        self.assertIn("mine", rescued.read().decode())

    def test_a_sheet_someone_else_is_editing_refuses_the_save(self):
        from toto.vault import locks

        vf = make_sheet(self.user, "grid")
        locks.acquire(vf, self.other)

        resp = self.client.post(
            reverse("primula:save", args=[vf.pk]),
            data=json.dumps(sheet_format.workbook_from_rows("grid", [["x"]])),
            content_type="application/json")

        self.assertEqual(resp.status_code, 423)

    def test_delete_removes_file_and_versions(self):
        vf = make_sheet(self.user, "grid")
        resp = self.client.post(reverse("primula:delete", args=[vf.pk]))
        self.assertRedirects(resp, reverse("primula:index"))
        self.assertFalse(VaultFile.objects.filter(pk=vf.pk).exists())
        self.assertFalse(FileVersion.objects.filter(file_id=vf.pk).exists())

    def test_delete_denied_for_non_owner(self):
        vf = make_sheet(self.other, "theirs", is_public=True)
        resp = self.client.post(reverse("primula:delete", args=[vf.pk]))
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(VaultFile.objects.filter(pk=vf.pk).exists())


class VaultRoutingTests(TestCase):
    """The vault "open" button routes sheet files into Primula, not raw ACE."""

    def test_sheet_plugin_registered_and_routes_to_primula(self):
        user = User.objects.create_user("ala", password="pw")
        vf = make_sheet(user, "routed")
        plugin = VaultEditorPlugin.for_file_type("sheet")
        self.assertIsNotNone(plugin)
        self.assertEqual(plugin.get_editor_url(vf), reverse("primula:edit", args=[vf.pk]))

    def test_json_still_routes_to_ace(self):
        # The generic .json path stays with toto.editor — Primula only claims "sheet".
        plugin = VaultEditorPlugin.for_file_type("json")
        self.assertIsNotNone(plugin)
        self.assertNotIn("primula", plugin.__class__.__module__)


class IngressTests(TestCase):
    def test_full_seeds_sample_sheets_idempotently(self):
        User.objects.create_user("admin", password="pw")
        call_command("ingress_primula", "--full", verbosity=0)
        sheets = VaultFile.objects.filter(file_type="sheet")
        self.assertEqual(sheets.count(), 2)
        for vf in sheets:
            self.assertTrue(sheet_format.is_sheet(_read_raw(vf)))
            self.assertEqual(vf.versions.count(), 1)
        # Re-run skips the existing files instead of duplicating them.
        call_command("ingress_primula", "--full", verbosity=0)
        self.assertEqual(VaultFile.objects.filter(file_type="sheet").count(), 2)

    def test_bare_ingress_seeds_nothing(self):
        User.objects.create_user("admin", password="pw")
        call_command("ingress_primula", verbosity=0)
        self.assertFalse(VaultFile.objects.filter(file_type="sheet").exists())
