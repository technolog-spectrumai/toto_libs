"""Transfer runs — the queued copy path, over the loopback harness.

Run only where a gate stanza names this module:

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.vault.tests_transfer
"""
import json
from unittest import mock, skipUnless

from django.apps import apps
from django.db import DatabaseError
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from . import mirror, scanning, transfer, transfer_dispatch, transfer_runner
from .models import Bucket, FileOrigin, VaultFile, VaultUsageEvent
from .peering import BucketGrant
from .storage import private_storage
from .storage_backends import LocalVaultStorageDriver, read_file_bytes
from .tests_mirror import DownHttp, LoopbackHttp, MirrorTestCase


class TransferTestCase(MirrorTestCase):
    """MirrorTestCase's two-host fixture, plus upload rights and a local
    bucket on the mounting side."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        BucketGrant.objects.filter(pk=cls.grant.pk).update(
            may_upload=True, may_delete=True)
        cls.grant.refresh_from_db()
        cls.local = Bucket.objects.create(
            name="local", slug="local", owner=cls.owner)

    def _local_file(self, key="mine", content=b"local bytes", bucket=None,
                    **kw):
        vf = VaultFile(owner=self.owner, title=f"{key}.txt", key=key,
                       file_type="text", bucket=bucket or self.local, **kw)
        from django.core.files.uploadedfile import SimpleUploadedFile
        vf.file.save(f"{key}.txt", SimpleUploadedFile(f"{key}.txt", content),
                     save=True)
        vf.content_hash = vf.create_hash()
        vf.save(update_fields=["content_hash"])
        return vf

    def _run(self, source, dest, files, **kw):
        return transfer.TransferRun.objects.create(
            owner=self.owner, source_bucket=source, dest_bucket=dest,
            file_ids=[f.pk for f in files], total_files=len(files),
            bytes_estimated=sum(f.file_size_bytes for f in files), **kw)

    def _execute(self, run, http=None):
        with mock.patch("toto.vault.peer_client._http",
                        return_value=http or LoopbackHttp()):
            return transfer_runner.execute_transfer_run(run.pk)

    def _mounted_stub(self, key="doc", content=b"remote bytes"):
        vf = self._source_file(key, content)
        vf.content_hash = vf.create_hash()
        vf.save(update_fields=["content_hash"])
        self._refresh()
        return VaultFile.objects.get(bucket=self.mounted, key=key)


class OutboundTransferTests(TransferTestCase):
    def test_local_to_remote_lands_on_the_peer(self):
        vf = self._local_file(content=b"outbound payload")
        run = self._execute(self._run(self.local, self.mounted, [vf]))
        self.assertEqual(run.status, transfer.TransferStatus.SUCCESS,
                         run.error)
        self.assertEqual(run.files_done, 1)
        self.assertEqual(run.bytes_done, len(b"outbound payload"))
        self.assertEqual(run.percent, 100)
        landed = VaultFile.objects.filter(
            bucket=self.source).exclude(pk=vf.pk).get()
        self.assertEqual(landed.owner, self.exporter)

    def test_encrypted_non_pdf_never_crosses(self):
        vf = self._local_file(key="sealed", is_encrypted=True)
        run = self._execute(self._run(self.local, self.mounted, [vf]))
        self.assertEqual(run.status, transfer.TransferStatus.SUCCESS)
        self.assertEqual(run.files_done, 0)
        self.assertEqual(run.files_skipped, 1)
        self.assertIn("not portable", run.skips[0]["reason"])
        self.assertFalse(VaultFile.objects.filter(
            bucket=self.source, title="sealed.txt").exists())

    def test_oversized_upload_is_skipped_not_failed(self):
        small = self._local_file(key="small", content=b"ok")
        big = self._local_file(key="big", content=b"way too many bytes")
        with mock.patch("toto.vault.storage_backends.EXTERNAL_UPLOAD_MAX_BYTES", 4):
            run = self._execute(
                self._run(self.local, self.mounted, [big, small]))
        self.assertEqual(run.status, transfer.TransferStatus.SUCCESS)
        self.assertEqual(run.files_done, 1)
        self.assertEqual(run.files_skipped, 1)
        self.assertIn("Too large", run.skips[0]["reason"])

    def test_hash_mismatch_is_skipped(self):
        vf = self._local_file()
        VaultFile.objects.filter(pk=vf.pk).update(content_hash="deadbeef")
        run = self._execute(self._run(self.local, self.mounted, [vf]))
        self.assertEqual(run.files_skipped, 1)
        self.assertIn("hash", run.skips[0]["reason"].lower())

    def test_dead_peer_fails_and_resume_finishes_without_double_billing(self):
        a = self._local_file(key="aa", content=b"aaaa")
        b = self._local_file(key="bb", content=b"bbbb")
        run = self._run(self.local, self.mounted, [a, b])

        real = LoopbackHttp()
        calls = {"n": 0}

        class FlakyHttp:
            def request(self, method, url, **kwargs):
                if method == "POST":
                    calls["n"] += 1
                    if calls["n"] >= 2:
                        raise ConnectionError("peer went away")
                return real.request(method, url, **kwargs)

        run = self._execute(run, http=FlakyHttp())
        self.assertEqual(run.status, transfer.TransferStatus.FAILED)
        self.assertEqual(run.files_done, 1)
        self.assertEqual(run.cursor, 1)

        # Retry: reopen and resume at the cursor — the first file is never
        # re-copied and never re-billed.
        run.status = transfer.TransferStatus.PENDING
        run.error = ""
        run.finished_at = None
        run.save()
        run = self._execute(run)
        self.assertEqual(run.status, transfer.TransferStatus.SUCCESS)
        self.assertEqual(run.files_done, 2)
        landed = VaultFile.objects.filter(bucket=self.source).count()
        self.assertEqual(landed, 2)
        events = VaultUsageEvent.objects.filter(
            idempotency_key__startswith=f"vault.transfer.mb:{run.pk}:")
        self.assertEqual(events.count(), 2)


class InboundTransferTests(TransferTestCase):
    def test_remote_to_local_copies_bytes_and_recomputes_hash(self):
        stub = self._mounted_stub(content=b"inbound payload")
        run = self._execute(self._run(self.mounted, self.local, [stub]))
        self.assertEqual(run.status, transfer.TransferStatus.SUCCESS,
                         run.error)
        landed = VaultFile.objects.get(bucket=self.local, key="doc")
        self.assertEqual(landed.origin, FileOrigin.NATIVE)
        from .storage_backends import read_file_bytes
        self.assertEqual(read_file_bytes(landed), b"inbound payload")
        import hashlib
        self.assertEqual(landed.content_hash,
                         hashlib.sha256(b"inbound payload").hexdigest())

    def test_conflict_policies(self):
        stub = self._mounted_stub()
        self._local_file(key="doc", content=b"already here")
        run = self._execute(self._run(
            self.mounted, self.local, [stub], copy_policy="fail"))
        self.assertEqual(run.files_skipped, 1)
        self.assertIn("already exists", run.skips[0]["reason"])
        run = self._execute(self._run(
            self.mounted, self.local, [stub], copy_policy="add_suffix"))
        self.assertEqual(run.files_done, 1)
        self.assertTrue(VaultFile.objects.filter(
            bucket=self.local, key="doc-1").exists())

    def test_scan_refusal_at_the_transfer_door_is_a_skip(self):
        stub = self._mounted_stub()
        from . import scanning as _scanning
        refused = _scanning.Verdict.refused("active-content", "bad")
        with mock.patch("toto.vault.scanning.should_scan",
                        return_value=True), \
             mock.patch("toto.vault.scanning.scan",
                        return_value=refused):
            run = self._execute(self._run(self.mounted, self.local, [stub]))
        self.assertEqual(run.files_skipped, 1)
        self.assertIn("scanner", run.skips[0]["reason"])
        self.assertFalse(VaultFile.objects.filter(
            bucket=self.local, key__startswith="doc").exists())

    def test_copy_log_written_once_with_landed_count(self):
        from .models import BucketCopyLog
        stub = self._mounted_stub()
        self._execute(self._run(self.mounted, self.local, [stub]))
        log = BucketCopyLog.objects.get(
            from_bucket=self.mounted, to_bucket=self.local)
        self.assertEqual(log.file_count, 1)


class CopyDelegationTests(TransferTestCase):
    def test_local_to_local_stays_synchronous(self):
        vf = self._local_file()
        other = Bucket.objects.create(
            name="other", slug="other", owner=self.owner)
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("vault:copy_files_ajax", args=["local"]),
            {"files": [vf.pk], "destination_bucket": other.pk})
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertNotIn("async", data)
        self.assertEqual(transfer.TransferRun.objects.count(), 0)
        self.assertTrue(VaultFile.objects.filter(bucket=other).exists())

    def test_remote_destination_delegates_to_a_run(self):
        # No celery worker listens in tests, so the delegation surfaces the
        # refuse-don't-inline branch: a run row exists, failed, named 503.
        vf = self._local_file()
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("vault:copy_files_ajax", args=["local"]),
            {"files": [vf.pk], "destination_bucket": self.mounted.pk})
        self.assertEqual(resp.status_code, 503)
        run = transfer.TransferRun.objects.get()
        self.assertEqual(run.status, transfer.TransferStatus.FAILED)
        self.assertEqual(run.file_ids, [vf.pk])
        self.assertIn("worker", resp.json()["error"].lower())

    def test_transfer_status_view_guards_and_answers(self):
        vf = self._local_file()
        run = self._run(self.local, self.mounted, [vf])
        from django.contrib.auth import get_user_model
        stranger = get_user_model().objects.create_user("s3", password="x")
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(
            reverse("vault:transfer_status", args=[run.pk])).status_code, 404)
        self.client.force_login(self.owner)
        payload = self.client.get(
            reverse("vault:transfer_status", args=[run.pk])).json()
        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["total_files"], 1)


class SweepPolicyTests(TransferTestCase):
    def test_transfer_run_has_a_stuck_run_policy(self):
        from toto.quota.sweeps import _REGISTRY
        labels = {p.model_label for p in _REGISTRY}
        self.assertIn("vault.TransferRun", labels)
        self.assertIn("vault.BucketRefreshRun", labels)


class LocalPairTestCase(TransferTestCase):
    """A second local bucket, so a run lands on this host's own disk."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other = Bucket.objects.create(name="other", slug="other", owner=cls.owner)

    def _listen(self, handler):
        from .signals import transfer_file_landed

        transfer_file_landed.connect(handler, weak=False, dispatch_uid="test-landing")
        self.addCleanup(transfer_file_landed.disconnect, dispatch_uid="test-landing")


class LandingSignalTests(LocalPairTestCase):
    """transfer_file_landed: once per landed file, inside its transaction."""

    def test_it_fires_once_per_landed_file_and_never_for_a_skip(self):
        heard = []
        self._listen(lambda sender, **kw: heard.append(
            (kw["source_file"].key, kw["new_file"].key, kw["dest_key"])))
        good = self._local_file(key="good", content=b"fine")
        bad = self._local_file(key="bad", content=b"real bytes")
        VaultFile.objects.filter(pk=bad.pk).update(content_hash="0" * 64)
        run = self._execute(self._run(self.local, self.other, [good, bad]))
        self.assertEqual(run.status, transfer.TransferStatus.SUCCESS, run.error)
        self.assertEqual(heard, [("good", "good", "good")])

    def test_a_refusing_listener_fails_the_run_and_lands_nothing(self):
        def refuse(sender, **kw):
            raise RuntimeError("notebook is full")

        self._listen(refuse)
        vf = self._local_file(key="refused", content=b"bytes")
        run = self._execute(self._run(self.local, self.other, [vf]))
        self.assertEqual(run.status, transfer.TransferStatus.FAILED)
        self.assertIn("notebook is full", run.error)
        self.assertEqual(run.cursor, 0)
        self.assertEqual(run.files_done, 0)
        self.assertFalse(VaultFile.objects.filter(bucket=self.other).exists())

    def test_a_soft_time_limit_stops_the_run_without_a_skip(self):
        from celery.exceptions import SoftTimeLimitExceeded

        vf = self._local_file(key="slow", content=b"bytes")
        run = self._run(self.local, self.other, [vf])
        with mock.patch("toto.vault.storage_backends.LocalVaultStorageDriver.read",
                        side_effect=SoftTimeLimitExceeded()):
            with self.assertRaises(SoftTimeLimitExceeded):
                self._execute(run)
        run.refresh_from_db()
        self.assertEqual((run.cursor, run.files_skipped), (0, 0))

    def test_a_replayed_file_is_charged_once(self):
        vf = self._local_file(key="paid", content=b"x" * 2048)
        run = self._run(self.local, self.other, [vf])
        with mock.patch("toto.quota.charge.charge") as charge:
            transfer_runner._meter(run, vf.pk, 2048, tariff=None)
            transfer_runner._meter(run, vf.pk, 2048, tariff=None)
        self.assertEqual(charge.call_count, 1)


class ReplaceLandingTests(LocalPairTestCase):
    """copy_policy 'replace': the old row goes with the landing, its bytes
    only after the landing commits."""

    def test_a_refused_replace_keeps_the_file_it_would_have_replaced(self):
        def refuse(sender, **kw):
            raise RuntimeError("notebook is full")

        self._listen(refuse)
        old = self._local_file(key="doc", content=b"old bytes", bucket=self.other)
        vf = self._local_file(key="doc", content=b"new bytes")
        run = self._execute(self._run(self.local, self.other, [vf],
                                      copy_policy="replace"))
        self.assertEqual(run.status, transfer.TransferStatus.FAILED)
        old.refresh_from_db()
        self.assertEqual(old.key, "doc")
        self.assertEqual(read_file_bytes(old), b"old bytes")

    def test_a_replace_removes_the_old_bytes_once_it_commits(self):
        old = self._local_file(key="doc", content=b"old bytes", bucket=self.other)
        vf = self._local_file(key="doc", content=b"new bytes")
        with self.captureOnCommitCallbacks(execute=True):
            run = self._execute(self._run(self.local, self.other, [vf],
                                          copy_policy="replace"))
        self.assertEqual(run.status, transfer.TransferStatus.SUCCESS, run.error)
        landed = VaultFile.objects.get(bucket=self.other, key="doc")
        self.assertNotEqual(landed.pk, old.pk)
        self.assertEqual(read_file_bytes(landed), b"new bytes")
        self.assertFalse(private_storage().exists(old.file.name))

    def test_a_soft_limit_inside_the_landing_takes_its_bytes_along(self):
        from celery.exceptions import SoftTimeLimitExceeded

        def stop(sender, **kw):
            raise SoftTimeLimitExceeded()

        self._listen(stop)
        vf = self._local_file(key="late", content=b"bytes")
        stored = []
        real_save = LocalVaultStorageDriver.save

        def save_and_note(driver, name, content):
            stored.append(real_save(driver, name, content))
            return stored[-1]

        with mock.patch.object(LocalVaultStorageDriver, "save", autospec=True,
                               side_effect=save_and_note):
            with self.assertRaises(SoftTimeLimitExceeded):
                self._execute(self._run(self.local, self.other, [vf]))
        self.assertEqual(len(stored), 1)
        self.assertFalse(private_storage().exists(stored[0]))
        self.assertFalse(VaultFile.objects.filter(bucket=self.other).exists())


class ClosedRunTests(LocalPairTestCase):
    """A run somebody else closed — the sweeper, or the next yamabiko pass —
    stays closed, and the runner stops at the next file."""

    def test_a_run_closed_mid_file_stops_before_the_next(self):
        first = self._local_file(key="first", content=b"one")
        VaultFile.objects.filter(pk=first.pk).update(content_hash="0" * 64)
        second = self._local_file(key="second", content=b"two")
        run = self._run(self.local, self.other, [first, second])
        real_read = LocalVaultStorageDriver.read

        def close_then_read(driver, name):
            if name == first.file.name:
                transfer_dispatch.fail_transfer_run(run.pk, "Closed by the next pass.")
            return real_read(driver, name)

        with mock.patch.object(LocalVaultStorageDriver, "read", autospec=True,
                               side_effect=close_then_read):
            result = self._execute(run)
        self.assertEqual(result.status, transfer.TransferStatus.FAILED)
        self.assertEqual(result.error, "Closed by the next pass.")
        self.assertEqual((result.cursor, result.files_skipped), (1, 1))
        self.assertFalse(VaultFile.objects.filter(bucket=self.other).exists())

    def test_a_run_closed_while_its_last_file_lands_stays_closed(self):
        vf = self._local_file(key="last", content=b"bytes")
        run = self._run(self.local, self.other, [vf])
        real_save = LocalVaultStorageDriver.save

        def close_then_save(driver, name, content):
            transfer_dispatch.fail_transfer_run(run.pk, "Closed by the sweeper.")
            return real_save(driver, name, content)

        with mock.patch.object(LocalVaultStorageDriver, "save", autospec=True,
                               side_effect=close_then_save):
            self._execute(run)
        run.refresh_from_db()
        self.assertEqual(run.status, transfer.TransferStatus.FAILED)
        self.assertEqual(run.error, "Closed by the sweeper.")
        self.assertEqual(run.files_done, 1)


class MeterTests(LocalPairTestCase):
    """Billing a landed file: only an existing usage event means billed."""

    def test_a_usage_event_that_cannot_be_written_still_charges(self):
        """The cursor is past the file once it landed: an unrecorded event must
        not make it free, because nothing would ever bill it later."""
        vf = self._local_file(key="paid", content=b"x" * 2048)
        with mock.patch.object(VaultUsageEvent, "save",
                               side_effect=DatabaseError("statement timeout")), \
                mock.patch("toto.quota.charge.charge") as charge:
            run = self._execute(self._run(self.local, self.other, [vf]))
        self.assertEqual(run.status, transfer.TransferStatus.SUCCESS, run.error)
        self.assertEqual(charge.call_count, 1)

    def test_a_charge_that_fails_fails_the_run(self):
        vf = self._local_file(key="paid", content=b"x" * 2048)
        with mock.patch("toto.quota.charge.charge", side_effect=RuntimeError("ledger down")):
            run = self._execute(self._run(self.local, self.other, [vf]))
        self.assertEqual(run.status, transfer.TransferStatus.FAILED)
        self.assertIn("Billing failed after 1 file(s)", run.error)

    def _assert_passed_on(self, patch):
        from celery.exceptions import SoftTimeLimitExceeded

        vf = self._local_file(key="paid", content=b"x" * 2048)
        run = self._run(self.local, self.other, [vf])
        with patch, self.assertRaises(SoftTimeLimitExceeded):
            self._execute(run)
        run.refresh_from_db()
        # Landed and left for a resume, not failed as a billing collapse.
        self.assertEqual((run.status, run.cursor),
                         (transfer.TransferStatus.RUNNING, 1))

    def test_a_soft_limit_while_charging_is_passed_on(self):
        from celery.exceptions import SoftTimeLimitExceeded

        self._assert_passed_on(mock.patch(
            "toto.quota.charge.charge", side_effect=SoftTimeLimitExceeded()))

    def test_a_soft_limit_while_recording_usage_is_passed_on(self):
        from celery.exceptions import SoftTimeLimitExceeded

        self._assert_passed_on(mock.patch.object(
            VaultUsageEvent, "save", side_effect=SoftTimeLimitExceeded()))


@skipUnless(apps.is_installed("toto.workflows"), "needs toto.workflows")
class WorkflowSoftLimitTests(LocalPairTestCase):
    def test_the_workflow_task_closes_a_run_its_soft_limit_stopped(self):
        from celery.exceptions import SoftTimeLimitExceeded

        from .predefined_tasks import vault_transfer_files

        vf = self._local_file(key="slow", content=b"bytes")
        run = self._run(self.local, self.other, [vf])
        with mock.patch.object(LocalVaultStorageDriver, "read",
                               side_effect=SoftTimeLimitExceeded()):
            with self.assertRaises(SoftTimeLimitExceeded):
                vault_transfer_files({"data": {"run_id": run.pk}})
        run.refresh_from_db()
        self.assertEqual(run.status, transfer.TransferStatus.FAILED)
        self.assertIn("time ran out", run.error)
        self.assertEqual(run.cursor, 0)


class PeerSoftLimitTests(TransferTestCase):
    def test_a_soft_limit_mid_download_neither_fails_the_run_nor_the_peer(self):
        from celery.exceptions import SoftTimeLimitExceeded

        class SlowHttp:
            def request(self, *args, **kwargs):
                raise SoftTimeLimitExceeded()

        stub = self._mounted_stub()
        run = self._run(self.mounted, self.local, [stub])
        with self.assertRaises(SoftTimeLimitExceeded):
            self._execute(run, http=SlowHttp())
        run.refresh_from_db()
        self.assertEqual(run.status, transfer.TransferStatus.RUNNING)
        self.peer.refresh_from_db()
        self.assertEqual(self.peer.last_error, "")


@skipUnless(apps.is_installed("toto.antivirus"), "needs toto.antivirus")
class ScanningSoftLimitTests(SimpleTestCase):
    """The scanning facade degrades on every failure but a worker's soft
    time limit, which is the worker's to handle."""

    def setUp(self):
        from celery.exceptions import SoftTimeLimitExceeded
        from toto.antivirus import engine

        self.stop, self.engine = SoftTimeLimitExceeded, engine

    def test_scan_passes_a_soft_limit_on(self):
        with mock.patch.object(self.engine, "scan", side_effect=self.stop()), \
                self.assertRaises(self.stop):
            scanning.scan(b"<svg/>", file_type="svg")

    def test_record_passes_a_soft_limit_on(self):
        with mock.patch.object(self.engine, "record", side_effect=self.stop()), \
                self.assertRaises(self.stop):
            scanning.record(None, scanning.Verdict.clean(), door="transfer")

    def test_should_scan_passes_a_soft_limit_on(self):
        from toto.antivirus.models import ScanPreference

        with mock.patch.object(ScanPreference, "applies", side_effect=self.stop()), \
                self.assertRaises(self.stop):
            scanning.should_scan(None, "svg", door="transfer")
