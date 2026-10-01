"""A vault peer's token never reaches the chain (2026-10-01, 37c.25).

The peer routes carry a grant's id and its magic token in the path
(``peer/<grant_uid>/<magic_token>/…``), and ``FileAuditMiddleware`` records
every download, upload and delete there with the request's path in
``request_source`` — sealed, never deleted, and part of the digest, so a
token written there could never be taken out. ``_scrub_path`` knew UUIDs and
password-reset tokens only; it now first cuts every value the error mail
stars (``toto.core.error_reports.path_secrets``).

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.audit.tests.test_path_secrets
"""

from __future__ import annotations

import json
import tempfile
import uuid

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import ResolverMatch, reverse

from toto.audit.models import AuditRecord
from toto.audit.services import request_source, verify_chain

TOKEN = "Zx81_peer-magic-token-0123456789abcdefABCDEF"


def _view(request):
    return None


def resolved(path, *, kwargs, url_name, namespaces=()):
    """A request the way a view sees it: its route already resolved."""
    request = RequestFactory().get(path)
    request.resolver_match = ResolverMatch(
        _view, (), kwargs, url_name=url_name, app_names=list(namespaces),
        namespaces=list(namespaces))
    return request


class PathRuleTests(SimpleTestCase):
    def test_a_peer_route_keeps_neither_the_grant_nor_its_token(self):
        grant = uuid.uuid4()
        request = resolved(f"/vault/peer/{grant}/{TOKEN}/manifest/",
                           kwargs={"grant_uid": grant, "magic_token": TOKEN},
                           url_name="peer_manifest", namespaces=["vault"])
        source = request_source(request)
        self.assertEqual(source["path"], "/vault/peer/[token]/[token]/manifest/")
        self.assertNotIn(TOKEN, json.dumps(source))

    def test_a_value_captured_as_a_token_is_cut_on_any_route(self):
        request = resolved("/things/abcdef123456/", kwargs={"token": "abcdef123456"},
                           url_name="thing")
        self.assertEqual(request_source(request)["path"], "/things/[token]/")

    def test_a_value_captured_under_an_ordinary_name_stays(self):
        request = resolved("/things/harvest-2026/", kwargs={"slug": "harvest-2026"},
                           url_name="thing")
        self.assertEqual(request_source(request)["path"], "/things/harvest-2026/")

    def test_a_route_the_rule_cannot_read_keeps_only_its_first_segment(self):
        request = RequestFactory().get(f"/vault/peer/{uuid.uuid4()}/{TOKEN}/manifest/")
        request.resolver_match = object()
        self.assertEqual(request_source(request)["path"], "/vault/[withheld]")

    def test_a_uuid_and_a_reset_token_still_go_on_a_path_no_route_takes(self):
        secret = "0f8fad5b-d9cb-469f-a165-70867728950e"
        reset = "c4x9qz-0123456789abcdef0123456789abcdef"
        source = request_source(RequestFactory().get(f"/nowhere/{secret}/{reset}/"))
        self.assertEqual(source["path"], "/nowhere/[uuid]/[token]/")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="audit-peer-path-"))
class PeerTrafficTests(TestCase):
    """The real peer doors, through the real middleware, read back off the chain."""

    @classmethod
    def setUpTestData(cls):
        from toto.vault.models import Bucket
        from toto.vault.peering import BucketGrant

        cls.owner = get_user_model().objects.create_user("exporter", password="x")
        cls.bucket = Bucket.objects.create(name="exported", slug="exported",
                                           owner=cls.owner)
        cls.grant = BucketGrant.objects.create(
            label="peer", bucket=cls.bucket,
            may_list=True, may_download=True, may_upload=True, may_delete=True)
        cls.raw_key = cls.grant.issue_api_key()
        cls.grant.save()

    def _url(self, name, **extra):
        return reverse(f"vault:{name}", kwargs={
            "grant_uid": self.grant.grant_uid,
            "magic_token": self.grant.magic_token, **extra})

    def _file(self, key="doc"):
        from toto.vault.models import VaultFile

        vf = VaultFile(owner=self.owner, title=f"{key}.txt", key=key,
                       file_type="text", bucket=self.bucket)
        vf.file.save(f"{key}.txt", SimpleUploadedFile(f"{key}.txt", b"bytes"),
                     save=True)
        return vf

    def assert_no_secret_on_the_chain(self, action, path):
        # One act, one record (a delete is FILE_TRASHED, from the trash).
        row = AuditRecord.objects.get(app_label="vault")
        self.assertEqual((row.action, row.request_source["path"]), (action, path))
        for written in AuditRecord.objects.all():
            text = json.dumps([written.request_source, written.metadata,
                               written.changes, written.object_id,
                               written.object_description])
            self.assertNotIn(self.grant.magic_token, text)
            self.assertNotIn(str(self.grant.grant_uid), text)
            self.assertNotIn(self.raw_key, text)
        self.assertTrue(verify_chain().ok)

    def test_a_download(self):
        self._file()
        response = self.client.get(self._url("peer_file_download", key="doc"),
                                   HTTP_X_VAULT_API_KEY=self.raw_key)
        self.assertEqual(response.status_code, 200)
        self.assert_no_secret_on_the_chain(
            "FILE_DOWNLOADED", "/vault/peer/[token]/[token]/files/doc/download/")

    def test_an_upload(self):
        response = self.client.post(self._url("peer_files"),
                                    {"file": SimpleUploadedFile("a.txt", b"abc")},
                                    HTTP_X_VAULT_API_KEY=self.raw_key)
        self.assertEqual(response.status_code, 201)
        self.assert_no_secret_on_the_chain(
            "VAULT_ACTION", "/vault/peer/[token]/[token]/files/")

    def test_a_delete(self):
        self._file()
        response = self.client.delete(self._url("peer_file_detail", key="doc"),
                                      HTTP_X_VAULT_API_KEY=self.raw_key)
        self.assertEqual(response.status_code, 200)
        self.assert_no_secret_on_the_chain(
            "FILE_TRASHED", "/vault/peer/[token]/[token]/files/doc/")

    def test_a_refused_download(self):
        """A wrong API key is a refusal on the chain — with the right token in
        the path, the half of the credential that was good."""
        self._file()
        response = self.client.get(self._url("peer_file_download", key="doc"),
                                   HTTP_X_VAULT_API_KEY="wrong-key")
        self.assertEqual(response.status_code, 403)
        self.assert_no_secret_on_the_chain(
            "FILE_DOWNLOADED", "/vault/peer/[token]/[token]/files/doc/download/")
        self.assertFalse(AuditRecord.objects.get(app_label="vault").success)
