"""The notebook's save, kernel start/stop and run-cell doors (2026-10-01):
signed in, POST, CSRF and the bucket's clearance gate — they were
csrf_exempt with no login check. RequestFactory, not the url: a host may
mount mandragora at no url and still carry the views."""

from __future__ import annotations

import json
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.middleware.csrf import CsrfViewMiddleware
from django.test import RequestFactory, TestCase, override_settings

from toto.mandragora import tpy_format, tpy_views
from toto.vault.models import Bucket, VaultFile

User = get_user_model()


class TpyDoorGateTests(TestCase):
    DOORS = ("tpy_save", "tpy_start_kernel", "tpy_stop_kernel", "tpy_run_cell")

    def setUp(self):
        override = override_settings(MEDIA_ROOT=tempfile.mkdtemp())
        override.enable()
        self.addCleanup(override.disable)
        self.alice = User.objects.create_user("alice", password="pass")
        self.bucket = Bucket.objects.create(name="Lab", slug="lab", owner=self.alice)
        body = tpy_format.dumps(tpy_format.new_notebook()).encode()
        self.vf = VaultFile.objects.create(
            owner=self.alice, title="nb.xml", file_type="xml", bucket=self.bucket,
            file=SimpleUploadedFile("nb.xml", body))
        kernel = mock.patch.object(tpy_views, "client")
        self.kernel = kernel.start()
        self.addCleanup(kernel.stop)
        self.kernel.start.return_value = {"state": "started"}
        self.kernel.stop.return_value = {"state": "stopped"}
        self.kernel.execute.return_value = {"stdout": "", "stderr": ""}

    def _request(self, user, method="post"):
        request = getattr(RequestFactory(), method)(
            "/x/", data=json.dumps({}) if method == "post" else None,
            content_type="application/json")
        request.user = user
        return request

    def _call(self, name, request):
        try:
            return getattr(tpy_views, name)(request, self.vf.pk).status_code
        except Http404:
            return 404

    def test_a_visitor_is_sent_to_sign_in(self):
        for name in self.DOORS:
            self.assertEqual(self._call(name, self._request(AnonymousUser())), 302, name)
        self.assertFalse(self.kernel.method_calls)

    def test_get_is_refused(self):
        for name in self.DOORS:
            self.assertEqual(self._call(name, self._request(self.alice, "get")), 405, name)
        self.assertFalse(self.kernel.method_calls)

    def test_a_post_without_the_csrf_token_is_refused(self):
        for name in self.DOORS:
            forged = self._request(self.alice)
            refused = CsrfViewMiddleware(lambda r: None).process_view(
                forged, getattr(tpy_views, name), (self.vf.pk,), {})
            self.assertEqual(getattr(refused, "status_code", None), 403, name)

    def test_an_owner_lacking_the_bucket_s_clearance_gets_404(self):
        from toto.socialhub.models import Clearance
        from toto.vault.models import BucketClearance

        payroll = Clearance.objects.create(name="payroll", slug="payroll")
        BucketClearance.objects.create(bucket=self.bucket, clearance=payroll)
        for name in self.DOORS:
            self.assertEqual(self._call(name, self._request(self.alice)), 404, name)
        self.assertFalse(self.kernel.method_calls)

    def test_the_owner_still_saves_and_runs(self):
        self.assertEqual(self._call("tpy_save", self._request(self.alice)), 200)
        self.assertEqual(self._call("tpy_run_cell", self._request(self.alice)), 200)
