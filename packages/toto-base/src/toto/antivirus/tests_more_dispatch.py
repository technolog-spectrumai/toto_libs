"""Getting an on-demand scan onto a worker (``dispatch``), closing one that
never will, and the storage-side façade's decisions (``toto.vault.scanning``)
that every door leans on: what is screened, what an owner's preference may
switch off, and which way every failure falls.
"""

import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from toto.antivirus import dispatch, engine
from toto.antivirus.models import RunStatus, ScanPreference, ScanResult, ScanRun, ScanVerdict
from toto.vault import scanning
from toto.vault.models import VaultFile
from toto.vault.scanning import Verdict

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="antivirus-more-dispatch-"))
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("owner", password="pw")

    def file(self, body=b"{}", file_type="json", content_hash="h1"):
        vault_file = VaultFile(owner=self.owner, title=f"f.{file_type}", file_type=file_type,
                               content_hash=content_hash)
        vault_file.file.save(f"f.{file_type}", ContentFile(body), save=False)
        vault_file.save()
        return vault_file


class DispatchTests(_Fixture):
    def test_the_row_exists_before_anything_is_queued(self):
        run = dispatch.create_run(user=self.owner, vault_file=self.file())
        self.assertEqual(run.status, RunStatus.PENDING)
        self.assertFalse(run.is_finished)

    def test_no_workflow_engine_refuses_by_name(self):
        run = dispatch.create_run(user=self.owner, vault_file=self.file())
        with patch.object(dispatch, "workflows_installed", return_value=False):
            with self.assertRaisesMessage(dispatch.CannotQueue, "BUILD_WORKFLOWS=1"):
                dispatch.dispatch_run(run)

    def test_no_worker_refuses_and_leaves_no_workflow_run(self):
        from toto.workflows.models import WorkflowRun

        run = dispatch.create_run(user=self.owner, vault_file=self.file())
        with patch("toto.celery_utils.celery_available", return_value=False):
            with self.assertRaisesMessage(dispatch.CannotQueue, "No worker is listening"):
                dispatch.dispatch_run(run)
        self.assertFalse(WorkflowRun.objects.exists())
        run.refresh_from_db()
        self.assertIsNone(run.workflow_run_id)

    def test_a_queued_run_remembers_its_workflow_run_and_task(self):
        from toto.workflows.models import WorkflowRun

        run = dispatch.create_run(user=self.owner, vault_file=self.file())
        task = SimpleNamespace(delay=lambda pk: calls.append(pk) or SimpleNamespace(id="task-42"))
        calls = []
        with patch("toto.celery_utils.celery_available", return_value=True), \
                patch("toto.workflows.tasks.start_workflow_run_task", task):
            dispatch.dispatch_run(run)
        run.refresh_from_db()
        workflow_run = WorkflowRun.objects.get(pk=run.workflow_run_id)
        self.assertEqual(workflow_run.input_data, {"data": {"run_id": run.pk}})
        self.assertEqual(workflow_run.started_by, self.owner)
        self.assertEqual(calls, [workflow_run.pk])
        self.assertEqual(run.task_id, "task-42")

    def test_failing_a_run_closes_it_with_a_reason(self):
        run = dispatch.create_run(user=self.owner, vault_file=self.file())
        dispatch.fail_run(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertEqual(run.error, "This scan was closed without finishing.")
        self.assertIsNotNone(run.finished_at)

    def test_failing_is_idempotent_and_never_reopens_a_finished_run(self):
        run = dispatch.create_run(user=self.owner, vault_file=self.file())
        run.finish(status=RunStatus.SUCCESS)
        dispatch.fail_run(run, "late sweeper")
        run.refresh_from_db()
        self.assertEqual((run.status, run.error), (RunStatus.SUCCESS, ""))
        dispatch.fail_run(987654)                        # a vanished run is a no-op


class FacadeDecisionTests(_Fixture):
    def test_only_the_known_text_shapes_and_pdf_are_screened(self):
        for file_type in ("svg", "html", "xml", "json", "pdf", "pxml"):
            self.assertTrue(scanning.is_scannable(file_type), file_type)
        for file_type in ("image", "video", "audio", "text", "zip", ""):
            self.assertFalse(scanning.is_scannable(file_type), file_type)

    def test_an_unscreened_type_comes_back_clean_but_unlooked_at(self):
        verdict = scanning.scan(b"<script>alert(1)</script>", file_type="text")
        self.assertEqual((verdict.ok, verdict.scanned), (True, False))

    def test_the_refusal_body_is_what_an_endpoint_returns(self):
        verdict = Verdict.refused("active-content", "a <script> element", line=3)
        self.assertEqual(verdict.as_error(), {"error": "Refused.", "reason": "active-content",
                                              "detail": "a <script> element", "line": 3})
        self.assertEqual(Verdict.clean(), Verdict(ok=True, scanned=True))

    def test_the_size_cap_follows_the_config_and_an_unreadable_config_is_none(self):
        with patch("toto.antivirus.scanners.config.params", return_value={"scan_max_mb": 2}):
            self.assertEqual(scanning.scan_size_cap_bytes(), 2 * 1024 * 1024)
        with patch("toto.antivirus.scanners.config.params", side_effect=RuntimeError("x")):
            self.assertIsNone(scanning.scan_size_cap_bytes())

    def test_over_the_cap_is_a_refusal_not_a_silent_pass(self):
        with patch("toto.antivirus.scanners.config.params", return_value={"scan_max_mb": 0.001}):
            verdict = scanning.scan("{" + " " * 2048 + "}", file_type="json")
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, "wrong-shape")
        self.assertIn("scan limit", verdict.detail)

    def test_pdf_text_handed_over_as_str_is_still_read_as_bytes(self):
        verdict = scanning.scan("%PDF-1.4\n/JavaScript (app.alert(1))\n%%EOF", file_type="pdf")
        self.assertFalse(verdict.ok)
        self.assertTrue(verdict.scanned)

    def test_a_worker_time_limit_is_let_through_not_swallowed(self):
        from toto.vault.scanning import SoftTimeLimitExceeded

        with patch("toto.antivirus.engine.scan", side_effect=SoftTimeLimitExceeded()):
            with self.assertRaises(SoftTimeLimitExceeded):
                scanning.scan(b"{}", file_type="json")
        with patch("toto.antivirus.models.ScanPreference.applies",
                   side_effect=SoftTimeLimitExceeded()):
            with self.assertRaises(SoftTimeLimitExceeded):
                scanning.should_scan(self.owner, "json")

    def test_a_broken_preference_lookup_scans(self):
        with patch("toto.antivirus.models.ScanPreference.applies",
                   side_effect=RuntimeError("db down")):
            self.assertTrue(scanning.should_scan(self.owner, "json", door="gateway"))

    def test_an_unscreenable_type_is_never_scanned_whatever_the_preference(self):
        self.assertFalse(scanning.should_scan(self.owner, "image", door="gateway"))

    def test_an_anonymous_owner_gets_everything_scanned(self):
        self.assertTrue(ScanPreference.applies(None, "svg", "gateway"))
        self.assertFalse(ScanPreference.applies(None, "image", "gateway"))

    def test_an_owner_who_narrowed_their_types_is_not_scanned_for_the_rest(self):
        ScanPreference.objects.create(user=self.owner, types=["svg"])
        self.assertTrue(scanning.should_scan(self.owner, "svg", door="gateway"))
        self.assertFalse(scanning.should_scan(self.owner, "json", door="gateway"))


class RecordingTests(_Fixture):
    def test_an_unscanned_verdict_leaves_no_row(self):
        f = self.file()
        scanning.record(f, Verdict.clean(scanned=False), user=self.owner, door="gateway")
        self.assertFalse(ScanResult.objects.exists())

    def test_a_file_with_no_hash_and_no_content_records_nothing(self):
        f = self.file(content_hash="")
        self.assertIsNone(engine.record(f, Verdict.clean(), user=self.owner))
        self.assertFalse(ScanResult.objects.exists())

    def test_the_hash_comes_from_the_content_looked_at_when_there_is_some(self):
        f = self.file(content_hash="stale")
        row = engine.record(f, Verdict.clean(), content="fresh body", door="editor")
        self.assertEqual(row.content_sha256, engine.digest("fresh body"))
        self.assertEqual(row.size_bytes, len("fresh body"))
        self.assertIsNone(row.scanned_by)

    def test_a_long_detail_is_cut_to_fit(self):
        f = self.file()
        row = engine.record(f, Verdict.refused("active-content", "x" * 5000))
        self.assertEqual(len(row.detail), 2000)
        self.assertEqual(row.verdict, ScanVerdict.REFUSED)

    def test_a_failure_to_read_is_recorded_and_a_later_scan_replaces_it(self):
        f = self.file(content_hash="h9")
        engine.record_failure(f, "permission denied", user=self.owner, door="manual")
        row = ScanResult.objects.get(file=f)
        self.assertEqual((row.verdict, row.reason), (ScanVerdict.ERROR, "unreadable"))
        engine.record(f, Verdict.clean(), user=self.owner)
        self.assertEqual(list(ScanResult.objects.filter(file=f).values_list("verdict", flat=True)),
                         [ScanVerdict.CLEAN])

    def test_a_crashing_bookkeeper_never_breaks_the_save(self):
        f = self.file()
        with patch("toto.antivirus.engine.record", side_effect=RuntimeError("db")):
            scanning.record(f, Verdict.clean(), user=self.owner)
        with patch("toto.antivirus.engine.clean_file_ids", side_effect=RuntimeError("db")):
            self.assertEqual(scanning.clean_file_ids([f]), set())
        with patch("toto.antivirus.engine.health_report", side_effect=RuntimeError("db")):
            self.assertIsNone(scanning.health_report([f]))

    def test_files_without_a_hash_are_never_ticked(self):
        self.assertEqual(engine.clean_file_ids([self.file(content_hash="")]), set())

    def test_an_encrypted_file_is_not_counted_as_scannable(self):
        sealed = self.file(file_type="json")
        VaultFile.objects.filter(pk=sealed.pk).update(is_encrypted=True)
        sealed.refresh_from_db()
        report = engine.health_report([sealed])
        self.assertEqual((report["scannable"], report["unscannable"], report["status"]),
                         (0, 1, "none"))
