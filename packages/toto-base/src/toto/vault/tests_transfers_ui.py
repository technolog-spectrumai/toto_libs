"""The transfer panel: listing, detail, and the retry door.

Run only where a gate stanza names this module:

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.vault.tests_transfers_ui
"""
import json

from django.contrib.auth import get_user_model
from django.urls import reverse

from toto.core.models import Platform

from . import transfer
from .tests_transfer import TransferTestCase

User = get_user_model()


class UiTransferTestCase(TransferTestCase):
    """TransferTestCase plus the Platform row PageProcessor pages need."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})


class PanelTests(UiTransferTestCase):
    def test_panel_lists_only_my_runs(self):
        vf = self._local_file()
        mine = self._run(self.local, self.mounted, [vf])
        other_user = User.objects.create_user("other", password="x")
        other_run = transfer.TransferRun.objects.create(
            owner=other_user, source_bucket=self.local,
            dest_bucket=self.mounted, file_ids=[vf.pk], total_files=1)
        self.client.force_login(self.owner)
        body = self.client.get(
            reverse("vault:transfer_panel")).content.decode()
        self.assertIn(reverse("vault:transfer_detail", args=[mine.pk]), body)
        self.assertNotIn(
            reverse("vault:transfer_detail", args=[other_run.pk]), body)

    def test_detail_is_owner_or_superuser_404(self):
        vf = self._local_file()
        run = self._run(self.local, self.mounted, [vf])
        url = reverse("vault:transfer_detail", args=[run.pk])
        stranger = User.objects.create_user("stranger9", password="x")
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.owner)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("transfer-initial", resp.content.decode())


class RetryTests(UiTransferTestCase):
    def _fail_run_after_first(self):
        """A two-file run where the peer died after the first landed."""
        a = self._local_file(key="ra", content=b"aaaa")
        b = self._local_file(key="rb", content=b"bbbb")
        run = self._run(self.local, self.mounted, [a, b])
        from .tests_mirror import LoopbackHttp

        real = LoopbackHttp()
        calls = {"n": 0}

        class FlakyHttp:
            def request(self, method, url, **kwargs):
                if method == "POST":
                    calls["n"] += 1
                    if calls["n"] >= 2:
                        raise ConnectionError("gone")
                return real.request(method, url, **kwargs)

        return self._execute(run, http=FlakyHttp()), b

    def test_retry_creates_a_new_run_with_the_remainder(self):
        run, remaining_file = self._fail_run_after_first()
        self.assertEqual(run.status, transfer.TransferStatus.FAILED)
        self.client.force_login(self.owner)
        # No worker in tests: the new run exists, failed with the sentence,
        # and the response is the refuse-don't-inline 503.
        resp = self.client.post(
            reverse("vault:transfer_retry", args=[run.pk]))
        self.assertEqual(resp.status_code, 503)
        new_run = transfer.TransferRun.objects.exclude(pk=run.pk).get()
        self.assertEqual(new_run.file_ids, [remaining_file.pk])
        self.assertEqual(new_run.copy_policy, run.copy_policy)
        # The old run's record is untouched — a retry is a NEW row.
        run.refresh_from_db()
        self.assertEqual(run.files_done, 1)

    def test_retry_reattempts_skipped_files_by_pk(self):
        sealed = self._local_file(key="sealed", is_encrypted=True)
        run = self._execute(self._run(self.local, self.mounted, [sealed]))
        self.assertEqual(run.files_skipped, 1)
        self.assertEqual(run.retry_file_ids(), [sealed.pk])

    def test_retry_refused_while_a_sibling_runs(self):
        run, _ = self._fail_run_after_first()
        vf = self._local_file(key="rc")
        transfer.TransferRun.objects.create(
            owner=self.owner, source_bucket=self.local,
            dest_bucket=self.mounted, file_ids=[vf.pk], total_files=1,
            status=transfer.TransferStatus.RUNNING)
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("vault:transfer_retry", args=[run.pk]))
        self.assertEqual(resp.status_code, 409)
        self.assertIn("already running", json.loads(resp.content)["error"])

    def test_nothing_to_retry_is_a_400(self):
        vf = self._local_file(key="done-file")
        run = self._execute(self._run(self.local, self.mounted, [vf]))
        self.assertEqual(run.status, transfer.TransferStatus.SUCCESS)
        self.assertEqual(run.files_skipped, 0)
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("vault:transfer_retry", args=[run.pk]))
        self.assertEqual(resp.status_code, 400)

    def test_unfinished_run_cannot_be_retried(self):
        vf = self._local_file(key="pending-file")
        run = self._run(self.local, self.mounted, [vf])
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("vault:transfer_retry", args=[run.pk]))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("still going", json.loads(resp.content)["error"])
