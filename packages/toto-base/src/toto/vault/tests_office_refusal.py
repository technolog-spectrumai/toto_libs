"""No Microsoft Office file enters the vault (stage 34, 2026-09-30).

The owner: ".docx, .xlsx or .pptx ---> REJECT. NO Microsoft here." One rule,
``models.upload_refusal``, asked by every door; by name AND by content, so a
.docx renamed to .zip is still refused, while a plain zip and an OpenDocument
file (a zip without [Content_Types].xml) are not.
"""

import io
import json
import tempfile
import zipfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import (Bucket, FileGateway, OFFICE_EXTENSIONS, VaultDirectory,
                     VaultFile, is_office_file, office_refusal_sentence,
                     upload_refusal)

User = get_user_model()

SENTENCE = "Microsoft Office files are not accepted here."


def _zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def ooxml(part="word/document.xml") -> bytes:
    return _zip({"[Content_Types].xml": "<Types/>", "_rels/.rels": "<Relationships/>",
                 part: "<x/>"})


OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504
ODT = _zip({"mimetype": "application/vnd.oasis.opendocument.text",
            "content.xml": "<office:document-content/>"})
PLAIN_ZIP = _zip({"notes.txt": "hello", "data/a.csv": "a,b\n1,2\n"})

#: The six the owner named, each with bytes of its own kind.
SIX = {
    "report.docx": ooxml("word/document.xml"),
    "sheet.xlsx": ooxml("xl/workbook.xml"),
    "deck.pptx": ooxml("ppt/presentation.xml"),
    "old.doc": OLE2,
    "old.xls": OLE2,
    "old.ppt": OLE2,
}


class TheRuleTests(TestCase):
    def test_every_office_extension_is_refused_by_name(self):
        for ext in (".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm",
                    ".dotx", ".xltx", ".potx", ".doc", ".xls", ".ppt"):
            with self.subTest(ext=ext):
                self.assertIn(ext, OFFICE_EXTENSIONS)
                self.assertTrue(is_office_file("a" + ext.upper()))

    def test_by_content_whatever_the_name(self):
        for part in ("word/document.xml", "xl/workbook.xml", "ppt/presentation.xml"):
            with self.subTest(part=part):
                self.assertTrue(is_office_file("innocent.zip", ooxml(part)))
        self.assertTrue(is_office_file("innocent.bin", OLE2))

    def test_a_file_object_is_read_and_put_back(self):
        upload = SimpleUploadedFile("x.zip", ooxml())
        upload.seek(3)
        self.assertTrue(is_office_file("x.zip", upload))
        self.assertEqual(upload.tell(), 3)

    def test_by_declared_type(self):
        self.assertTrue(is_office_file(
            "x", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
        self.assertTrue(is_office_file("x", mime="application/msword"))
        self.assertTrue(is_office_file("x", mime="application/vnd.ms-excel"))

    def test_open_document_and_a_plain_zip_are_not_office(self):
        for name, data in (("letter.odt", ODT), ("sheet.ods", ODT), ("talk.odp", ODT),
                           ("bundle.zip", PLAIN_ZIP), ("notes.txt", b"PK but not a zip")):
            with self.subTest(name=name):
                self.assertFalse(is_office_file(name, data))
        self.assertFalse(is_office_file(
            "x", mime="application/vnd.oasis.opendocument.text"))

    def test_the_sentence_says_why_and_what_instead(self):
        self.assertEqual(upload_refusal("a.docx"), office_refusal_sentence())
        self.assertIn(SENTENCE, office_refusal_sentence())
        self.assertIn("OpenDocument", office_refusal_sentence())
        self.assertEqual(upload_refusal("a.txt", file_type="text"), "")

    @override_settings(VAULT_REFUSED_FILE_TYPES={"latex"})
    def test_the_host_list_still_answers_after_office(self):
        self.assertIn("latex", upload_refusal("a.tex", file_type="latex"))


_MEDIA = tempfile.mkdtemp(prefix="vault-office-")


@override_settings(MEDIA_ROOT=_MEDIA)
class DoorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("officeless", "o@x.com", "pw")
        cls.bucket = Bucket.objects.create(name="Gate", owner=cls.user, slug="office-gate")
        cls.directory = VaultDirectory.objects.create(
            bucket=cls.bucket, name="inbox", owner=cls.user)
        FileGateway.objects.create(directory=cls.directory, bucket=cls.bucket, name="gate")

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    # -- the API upload -------------------------------------------------------
    def _api(self, name, data):
        return self.client.post(reverse("vault:api_file_upload"),
                                {"file": SimpleUploadedFile(name, data)})

    def test_api_upload_refuses_the_six_with_the_sentence(self):
        for name, data in SIX.items():
            with self.subTest(name=name):
                resp = self._api(name, data)
                self.assertEqual(resp.status_code, 400, resp.content)
                self.assertIn(SENTENCE, resp.json()["error"])
        self.assertFalse(VaultFile.objects.exists())

    def test_api_upload_refuses_a_renamed_docx(self):
        resp = self._api("harmless.zip", ooxml())
        self.assertEqual(resp.status_code, 400)
        self.assertIn(SENTENCE, resp.json()["error"])

    def test_a_csv_windows_declares_as_excel_is_taken(self):
        # Review, 2026-10-01: the declared type alone refused it.
        resp = self.client.post(reverse("vault:api_file_upload"), {
            "file": SimpleUploadedFile("data.csv", b"a,b\n1,2\n",
                                       content_type="application/vnd.ms-excel")})
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_api_upload_takes_a_plain_zip_and_an_odt(self):
        self.assertEqual(self._api("bundle.zip", PLAIN_ZIP).status_code, 201)
        self.assertEqual(self._api("letter.odt", ODT).status_code, 201)
        self.assertEqual(VaultFile.objects.count(), 2)

    # -- the gateway (the file page's upload) ----------------------------------
    def _gateway(self, name, data):
        resp = self.client.post(reverse("vault:gateway_upload", args=[self.directory.pk]),
                                {"file": SimpleUploadedFile(name, data)})
        return resp.json()

    def test_gateway_refuses_the_six_with_the_sentence(self):
        for name, data in SIX.items():
            with self.subTest(name=name):
                payload = self._gateway(name, data)
                self.assertIn(SENTENCE, " ".join(payload.get("errors") or []))
        self.assertFalse(VaultFile.objects.exists())

    def test_gateway_refuses_a_renamed_docx_and_takes_the_others(self):
        payload = self._gateway("harmless.zip", ooxml("xl/workbook.xml"))
        self.assertIn(SENTENCE, " ".join(payload.get("errors") or []))
        self._gateway("bundle.zip", PLAIN_ZIP)
        self._gateway("letter.odt", ODT)
        self.assertEqual(sorted(VaultFile.objects.values_list("title", flat=True)),
                         ["bundle.zip", "letter.odt"])

    def test_a_manual_type_does_not_get_one_in(self):
        resp = self.client.post(
            reverse("vault:gateway_upload", args=[self.directory.pk]),
            {"file": SimpleUploadedFile("x.zip", ooxml()), "file_type": "text"})
        self.assertIn(SENTENCE, " ".join(resp.json().get("errors") or []))
        self.assertFalse(VaultFile.objects.exists())

    # -- names: rename, New file, the API's create ------------------------------
    def test_rename_to_an_office_name_is_refused(self):
        self._api("note.txt", b"hello")
        vf = VaultFile.objects.get()
        for title in ("note.docx", "note.xls"):
            with self.subTest(title=title):
                resp = self.client.post(reverse("vault:rename_file"),
                                        {"file_pk": vf.pk, "title": title})
                self.assertEqual(resp.status_code, 400)
                self.assertIn(SENTENCE, resp.json()["error"])
        vf.refresh_from_db()
        self.assertEqual(vf.title, "note")  # the API upload drops the extension

    # Both flags that take the New-file door away are set to "there": which
    # sentence the door says can only be asked where there is a door. A host
    # with VAULT_STORAGE_ONLY (zenobia, 2026-10-03) answers 404 for this name
    # as for any other — tests_hardening.StorageOnlyFlagTests.
    @override_settings(VAULT_STORAGE_ONLY=False, VAULT_FILE_EDITS=True)
    def test_new_file_with_an_office_name_is_refused(self):
        resp = self.client.post(reverse("vault:create_file"), {
            "title": "minutes.docx", "file_type": "text",
            "directory_id": self.directory.pk})
        self.assertEqual(resp.status_code, 400)
        self.assertIn(SENTENCE, resp.json()["error"])
        self.assertFalse(VaultFile.objects.exists())

    def test_api_create_with_an_office_name_is_refused(self):
        resp = self.client.post(
            reverse("vault:api_file_create"),
            data=json.dumps({"title": "budget.xlsx", "file_type": "text",
                             "bucket_slug": self.bucket.slug, "content": "a"}),
            content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn(SENTENCE, resp.json()["error"])


@override_settings(MEDIA_ROOT=_MEDIA)
class PeerDoorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .peering import BucketGrant

        cls.owner = User.objects.create_user("exporter-office", password="x")
        cls.bucket = Bucket.objects.create(name="exported", slug="exported-office",
                                           owner=cls.owner)
        cls.grant = BucketGrant.objects.create(
            label="peer", bucket=cls.bucket,
            may_list=True, may_download=True, may_upload=True, may_delete=True)
        cls.raw_key = cls.grant.issue_api_key()
        cls.grant.save()

    def _post(self, name, data):
        url = reverse("vault:peer_files", kwargs={
            "grant_uid": self.grant.grant_uid, "magic_token": self.grant.magic_token})
        return self.client.post(url, {"file": SimpleUploadedFile(name, data)},
                                HTTP_X_VAULT_API_KEY=self.raw_key)

    def test_a_peer_cannot_push_one_either(self):
        for name, data in list(SIX.items()) + [("harmless.zip", ooxml())]:
            with self.subTest(name=name):
                resp = self._post(name, data)
                self.assertEqual(resp.status_code, 400)
                self.assertIn(SENTENCE, resp.json()["error"])
        self.assertEqual(self._post("bundle.zip", PLAIN_ZIP).status_code, 201)


class MirrorTests(TestCase):
    def test_a_peers_office_file_is_not_listed_here(self):
        from .mirror import _upsert_stub

        owner = User.objects.create_user("mirrorer", password="x")
        bucket = Bucket.objects.create(name="m", slug="m-office", owner=owner)
        run, seen = mock.Mock(), set()
        _upsert_stub(run, bucket, {"key": "report-docx", "title": "report.docx",
                                   "file_type": "xml"}, seen)
        self.assertEqual(seen, set())
        self.assertFalse(VaultFile.all_objects.filter(bucket=bucket).exists())
        self.assertIn(SENTENCE, run.add_skip.call_args[0][1])


@override_settings(MEDIA_ROOT=_MEDIA)
class ReviewDoorTests(TestCase):
    """Two doors the stage's review found open (2026-10-01): the API's rename,
    and a copy between two local buckets, which never meets the transfer
    runner's check."""

    @classmethod
    def setUpTestData(cls):
        from django.core.files.base import ContentFile

        from toto.core.models import Platform

        # The copy page is rendered through the page processor, which needs one.
        Platform.objects.create(site_name="Test", author="Test",
                                publication_year=2024, active=True)
        cls.user = User.objects.create_user("copier-office", password="pw")
        cls.src = Bucket.objects.create(name="Src", owner=cls.user, slug="office-src")
        cls.dst = Bucket.objects.create(name="Dst", owner=cls.user, slug="office-dst")
        cls.note = VaultFile(owner=cls.user, title="note.txt", key="note",
                             file_type="text", bucket=cls.src)
        cls.note.file.save("note.txt", ContentFile(b"hello"), save=True)
        # A row from before the rule.
        cls.old = VaultFile(owner=cls.user, title="minutes.docx", key="minutes-docx",
                            file_type="xml", bucket=cls.src)
        cls.old.file.save("minutes.docx", ContentFile(ooxml()), save=True)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    def test_the_api_rename_refuses_an_office_name(self):
        url = reverse("vault:api_file_detail", args=[self.note.key])
        resp = self.client.patch(url, json.dumps({"title": "note.docx"}),
                                 content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn(SENTENCE, resp.json()["error"])
        self.note.refresh_from_db()
        self.assertEqual(self.note.title, "note.txt")
        resp = self.client.patch(url, json.dumps({"title": "note.md"}),
                                 content_type="application/json")
        self.assertEqual(resp.status_code, 200)

    def test_the_copy_page_refuses_an_office_row(self):
        resp = self.client.post(reverse("vault:copy_files", args=[self.src.slug]),
                                {"files": [self.note.pk, self.old.pk],
                                 "destination_bucket": self.dst.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "minutes.docx")
        self.assertFalse(VaultFile.objects.filter(bucket=self.dst).exists())

    def test_the_copy_dialog_refuses_an_office_row(self):
        url = reverse("vault:copy_files_ajax", args=[self.src.slug])
        resp = self.client.post(url, {"files": [self.old.pk],
                                      "destination_bucket": self.dst.pk})
        self.assertEqual(resp.status_code, 400)
        self.assertIn(SENTENCE, resp.json()["error"])
        self.assertFalse(VaultFile.objects.filter(bucket=self.dst).exists())
        resp = self.client.post(url, {"files": [self.note.pk],
                                      "destination_bucket": self.dst.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(VaultFile.objects.filter(bucket=self.dst).count(), 1)
