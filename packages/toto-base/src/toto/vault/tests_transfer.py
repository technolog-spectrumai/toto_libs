"""Transfer runs — the queued copy path, over the loopback harness.

Run only where a gate stanza names this module:

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.vault.tests_transfer
"""
import json
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from . import mirror, transfer, transfer_runner
from .models import Bucket, FileOrigin, VaultFile, VaultUsageEvent
from .peering import BucketGrant
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

    def _local_file(self, key="mine", content=b"local bytes", **kw):
        vf = VaultFile(owner=self.owner, title=f"{key}.txt", key=key,
                       file_type="text", bucket=self.local, **kw)
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
    def _mounted_stub(self, key="doc", content=b"remote bytes"):
        vf = self._source_file(key, content)
        vf.content_hash = vf.create_hash()
        vf.save(update_fields=["content_hash"])
        self._refresh()
        return VaultFile.objects.get(bucket=self.mounted, key=key)

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
