"""The writer's smaller doors: its refusals, the source view, media, renditions.

`test_views` covers the happy path of opening and saving a document. These pin
what each endpoint REFUSES — an encrypted file, a stranger, a body too big or
not UTF-8, a host that switched file editing off, somebody else holding the
lock — and the media endpoints, which inline a vault image or an upload into a
document and must never hand back what the vault would not.
"""

import base64
import io
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from toto.cyprian import htmldoc as df
from toto.cyprian import media
from toto.vault.models import VaultDirectory, VaultFile

from .base import CyprianTestCase


def _png(size=(10, 10), mode="RGBA", colour=(255, 0, 0, 128)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new(mode, size, colour).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(size=(1200, 900)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (0, 128, 255)).save(buf, format="JPEG")
    return buf.getvalue()


def _decoded_size(data_uri: str):
    from PIL import Image

    raw = base64.b64decode(data_uri.split(",", 1)[1])
    return Image.open(io.BytesIO(raw)).size


class Fixture(CyprianTestCase):
    def document(self, *, owner=None, title="report.html", raw=None, encrypted=False,
                 file_type="html", public=False):
        body = raw if raw is not None else df.dumps(df.new_document("Report")).encode()
        return VaultFile.objects.create(
            owner=owner or self.owner, title=title, file_type=file_type,
            is_public=public, is_encrypted=encrypted, bucket=self.bucket,
            file=SimpleUploadedFile(title, body))

    def image(self, title, raw, *, owner=None, file_type="image", public=False,
              encrypted=False, directory=None, bucket=None):
        return VaultFile.objects.create(
            owner=owner or self.owner, title=title, file_type=file_type,
            is_public=public, is_encrypted=encrypted, directory=directory,
            bucket=bucket or self.bucket, file=SimpleUploadedFile(title, raw))


class WriterRefusalTests(Fixture):
    def test_an_encrypted_document_shows_its_owner_the_locked_page(self):
        sealed = self.document(raw=b"ciphertext", encrypted=True)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("cyprian:edit", args=[sealed.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "vault/encrypted_locked.html")

    def test_an_encrypted_document_is_a_404_to_anyone_else(self):
        sealed = self.document(raw=b"ciphertext", encrypted=True, public=True)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("cyprian:edit", args=[sealed.pk])).status_code,
                         404)

    def test_a_file_that_is_not_utf8_does_not_open_in_the_writer(self):
        binary = self.document(raw=b"\xff\xfe\x00binary")
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("cyprian:edit", args=[binary.pk])).status_code,
                         404)

    def test_a_file_that_is_not_html_does_not_open_in_the_writer(self):
        text = self.document(title="notes.txt", file_type="text", raw=b"just text")
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("cyprian:edit", args=[text.pk])).status_code,
                         404)

    def test_a_bare_fragment_opens_as_a_document(self):
        # Hand-edited in ACE, no <html> around it: still the writer's.
        fragment = self.document(raw=b"<p>just a paragraph</p>")
        self.client.force_login(self.owner)
        response = self.client.get(reverse("cyprian:edit", args=[fragment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("just a paragraph", response.context["document_json"]["content"])

    def test_a_public_document_is_still_not_someone_elses_to_write(self):
        # Reading is not writing: with no owning app's bridge, only the owner edits.
        public = self.document(public=True)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("cyprian:edit", args=[public.pk])).status_code,
                         404)

    def test_the_media_picker_lists_what_the_writer_may_read_and_nothing_else(self):
        folder = VaultDirectory.objects.create(bucket=self.bucket, owner=self.owner,
                                               name="figures")
        mine = self.image("chart.png", _png(), directory=folder)
        sealed = self.image("sealed.png", b"ciphertext", encrypted=True)
        from toto.vault.models import Bucket

        elsewhere = Bucket.objects.create(owner=self.other, name="Theirs", slug="theirs",
                                          storage_backend="local")
        shared = self.image("shared.svg", b"<svg/>", owner=self.other, file_type="svg",
                            public=True, bucket=elsewhere)
        private = self.image("private.png", _png(), owner=self.other, bucket=elsewhere)
        doc = self.document()
        self.client.force_login(self.owner)
        picker = self.client.get(reverse("cyprian:edit", args=[doc.pk])).context[
            "vault_media_json"]
        by_pk = {row["pk"]: row for row in picker}
        self.assertEqual(by_pk[mine.pk]["location"], "Papers / figures")
        self.assertIn(shared.pk, by_pk)
        self.assertNotIn(sealed.pk, by_pk)
        self.assertNotIn(private.pk, by_pk)


class SaveRefusalTests(Fixture):
    def post(self, vault_file, body, **extra):
        return self.client.post(reverse("cyprian:save", args=[vault_file.pk]),
                                data=body, content_type="application/json", **extra)

    def test_saving_is_refused_when_the_host_switched_file_edits_off(self):
        doc = self.document()
        before = doc.file.read()
        self.client.force_login(self.owner)
        with override_settings(VAULT_FILE_EDITS=False):
            response = self.post(doc, json.dumps({"document": {"title": "x"}}))
        self.assertEqual(response.status_code, 403)
        self.assertIn("disabled", response.json()["error"])
        doc.refresh_from_db()
        doc.file.open("rb")
        self.assertEqual(doc.file.read(), before)

    def test_an_encrypted_document_cannot_be_saved_over(self):
        sealed = self.document(raw=b"ciphertext", encrypted=True)
        self.client.force_login(self.owner)
        response = self.post(sealed, json.dumps({"document": {"title": "x"}}))
        self.assertEqual(response.status_code, 403)

    def test_a_body_that_is_not_json_is_a_400(self):
        doc = self.document()
        self.client.force_login(self.owner)
        response = self.post(doc, "{not json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid JSON", response.json()["error"])

    @override_settings(CYPRIAN_MAX_DOCUMENT_BYTES=2 * 1024 * 1024)
    def test_a_body_over_the_limit_is_413_and_nothing_is_written(self):
        doc = self.document()
        before = doc.file.read()
        self.client.force_login(self.owner)
        payload = json.dumps({"document": {"title": "big",
                                           "content": "<p>" + "x" * (3 * 1024 * 1024) + "</p>"}})
        response = self.post(doc, payload)
        self.assertEqual(response.status_code, 413)
        self.assertIn("2 MB", response.json()["error"])
        doc.refresh_from_db()
        doc.file.open("rb")
        self.assertEqual(doc.file.read(), before)

    def test_a_save_is_refused_while_someone_else_holds_the_lock(self):
        from toto.vault import locks

        doc = self.document()
        locks.acquire(doc, self.other)
        self.client.force_login(self.owner)
        response = self.post(doc, json.dumps({"document": {"title": "mine now"}}))
        self.assertEqual(response.status_code, 423)
        self.assertEqual(response.json()["locked_by"], "stranger")

    def test_a_save_answers_with_the_hash_of_what_it_wrote(self):
        import hashlib

        doc = self.document()
        self.client.force_login(self.owner)
        response = self.post(doc, json.dumps({"document": {"title": "T",
                                                           "content": "<p>new</p>"}}))
        self.assertEqual(response.status_code, 200)
        doc.refresh_from_db()
        doc.file.open("rb")
        self.assertEqual(response.json()["content_hash"],
                         hashlib.sha256(doc.file.read()).hexdigest())

    def test_script_in_a_saved_body_does_not_reach_the_file(self):
        doc = self.document()
        self.client.force_login(self.owner)
        self.post(doc, json.dumps({"document": {
            "title": "T", "content": "<p onclick=\"x()\">ok</p><script>bad()</script>"}}))
        doc.refresh_from_db()
        doc.file.open("rb")
        stored = doc.file.read().decode()
        self.assertIn("ok", stored)
        self.assertNotIn("<script>bad()", stored)
        self.assertNotIn("onclick", stored)

    def test_an_anonymous_save_is_refused(self):
        doc = self.document()
        response = self.post(doc, json.dumps({"document": {"title": "x"}}))
        self.assertEqual(response.status_code, 401)


class SourceViewRefusalTests(Fixture):
    def test_an_empty_file_is_not_a_document(self):
        empty = self.document(raw=b"   ")
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("cyprian:source", args=[empty.pk])).status_code,
                         404)

    def test_the_source_of_an_encrypted_document_is_refused(self):
        sealed = self.document(raw=b"ciphertext", encrypted=True)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("cyprian:source", args=[sealed.pk])).status_code,
                         403)

    def test_posted_source_that_is_not_utf8_is_a_400(self):
        doc = self.document()
        self.client.force_login(self.owner)
        response = self.client.post(reverse("cyprian:source", args=[doc.pk]),
                                    data=b"\xff\xfe<p>", content_type="text/plain")
        self.assertEqual(response.status_code, 400)

    @override_settings(CYPRIAN_MAX_DOCUMENT_BYTES=1024 * 1024)
    def test_posted_source_over_the_limit_is_a_413(self):
        doc = self.document()
        self.client.force_login(self.owner)
        response = self.client.post(reverse("cyprian:source", args=[doc.pk]),
                                    data=b"x" * (1024 * 1024 + 1), content_type="text/plain")
        self.assertEqual(response.status_code, 413)

    def test_the_source_view_answers_only_get_and_post(self):
        doc = self.document()
        self.client.force_login(self.owner)
        response = self.client.put(reverse("cyprian:source", args=[doc.pk]), data=b"x")
        self.assertEqual(response.status_code, 405)

    def test_posted_source_is_sanitised_on_the_way_back(self):
        doc = self.document()
        self.client.force_login(self.owner)
        response = self.client.post(reverse("cyprian:source", args=[doc.pk]),
                                    data=b"<p>kept</p><script>bad()</script>",
                                    content_type="text/plain")
        content = response.json()["document"]["content"]
        self.assertIn("kept", content)
        self.assertNotIn("<script", content)


class MediaEmbedTests(Fixture):
    def embed(self, user, pk):
        self.client.force_login(user)
        return self.client.get(reverse("cyprian:media_embed"), {"file_pk": pk})

    def test_a_file_pk_is_required(self):
        self.client.force_login(self.owner)
        for params in ({}, {"file_pk": "abc"}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(reverse("cyprian:media_embed"),
                                                 params).status_code, 400)

    def test_an_svg_is_embedded_as_sanitised_markup(self):
        svg = self.image("diagram.svg", b'<svg><script>bad()</script><rect width="1"/></svg>',
                         file_type="svg")
        data = self.embed(self.owner, svg.pk).json()
        self.assertEqual((data["kind"], data["alt"]), ("svg", "diagram"))
        self.assertNotIn("script", data["payload"])
        self.assertIn("<rect", data["payload"])

    def test_a_raster_is_embedded_as_a_data_uri_scaled_to_fit(self):
        photo = self.image("photo.jpg", _jpeg())
        data = self.embed(self.owner, photo.pk).json()
        self.assertEqual(data["kind"], "image")
        self.assertTrue(data["payload"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(max(_decoded_size(data["payload"])), media.MAX_DIM)

    def test_someone_elses_private_image_is_a_404_and_their_public_one_is_not(self):
        from toto.vault.models import Bucket

        elsewhere = Bucket.objects.create(owner=self.other, name="Theirs", slug="theirs",
                                          storage_backend="local")
        private = self.image("private.png", _png(), owner=self.other, bucket=elsewhere)
        public = self.image("public.png", _png(), owner=self.other, bucket=elsewhere,
                            public=True)
        self.assertEqual(self.embed(self.owner, private.pk).status_code, 404)
        self.assertEqual(self.embed(self.owner, public.pk).status_code, 200)

    def test_an_image_in_a_bucket_kept_to_a_clearance_you_lack_cannot_be_embedded(self):
        from toto.people.models import Person
        from toto.socialhub.models import Clearance
        from toto.vault.models import Bucket, BucketClearance

        internal = Clearance.objects.create(name="internal", slug="internal")
        Person.objects.create(user=self.owner, display_name="W")
        elsewhere = Bucket.objects.create(owner=self.other, name="Theirs", slug="theirs",
                                          storage_backend="local")
        BucketClearance.objects.create(bucket=elsewhere, clearance=internal)
        kept = self.image("kept.png", _png(), owner=self.other, bucket=elsewhere,
                          public=True)
        self.assertEqual(self.embed(self.owner, kept.pk).status_code, 404)
        self.owner.community_profile.clearances.add(internal)
        self.assertEqual(self.embed(self.owner, kept.pk).status_code, 200)

    def test_your_own_image_in_a_bucket_kept_to_a_clearance_you_lack_cannot_be_embedded(self):
        from toto.socialhub.models import Clearance
        from toto.vault.models import Bucket, BucketClearance

        internal = Clearance.objects.create(name="internal", slug="internal")
        kept_bucket = Bucket.objects.create(owner=self.owner, name="Kept", slug="kept",
                                            storage_backend="local")
        BucketClearance.objects.create(bucket=kept_bucket, clearance=internal)
        mine = self.image("mine.png", _png(), owner=self.owner, bucket=kept_bucket)
        self.assertEqual(self.embed(self.owner, mine.pk).status_code, 404)

    def test_an_encrypted_image_cannot_be_embedded(self):
        sealed = self.image("sealed.png", b"ciphertext", encrypted=True)
        self.assertEqual(self.embed(self.owner, sealed.pk).status_code, 404)

    def test_a_document_is_not_an_embeddable_file(self):
        doc = self.document()
        self.assertEqual(self.embed(self.owner, doc.pk).status_code, 404)


class MediaUploadTests(Fixture):
    def upload(self, name, raw, content_type="application/octet-stream"):
        self.client.force_login(self.owner)
        return self.client.post(reverse("cyprian:media_upload"),
                                {"file": SimpleUploadedFile(name, raw, content_type)})

    def test_no_file_is_a_400(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(reverse("cyprian:media_upload")).status_code, 400)

    @override_settings(CYPRIAN_MAX_UPLOAD_BYTES=100)
    def test_a_file_over_the_cap_is_a_413(self):
        self.assertEqual(self.upload("big.png", _png(size=(200, 200))).status_code, 413)

    def test_an_svg_upload_comes_back_sanitised(self):
        data = self.upload("logo.svg", b'<svg onload="bad()"><circle r="1"/></svg>').json()
        self.assertEqual((data["kind"], data["alt"]), ("svg", "logo"))
        self.assertNotIn("onload", data["payload"])

    def test_something_that_is_not_an_image_is_refused(self):
        response = self.upload("notes.txt", b"plain words")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Only images", response.json()["error"])

    def test_a_png_keeps_its_format_and_transparency(self):
        data = self.upload("dot.png", _png()).json()
        self.assertTrue(data["payload"].startswith("data:image/png;base64,"))

    def test_a_get_is_refused(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("cyprian:media_upload")).status_code, 405)


class RenditionTests(Fixture):
    def fetch(self, user, vault_file):
        self.client.force_login(user)
        return self.client.get(reverse("cyprian:rendition", args=[vault_file.pk]))

    def test_the_owner_gets_a_pdf_inline(self):
        pdf = self.document(title="report.pdf", file_type="pdf", raw=b"%PDF-1.4 fake")
        response = self.fetch(self.owner, pdf)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["Content-Disposition"], 'inline; filename="report.pdf"')
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 fake")

    def test_an_html_rendition_is_served_as_html(self):
        page = self.document(title="report.html")
        self.assertEqual(self.fetch(self.owner, page)["Content-Type"],
                         "text/html; charset=utf-8")

    def test_a_rendition_is_private_even_when_the_file_is_public(self):
        pdf = self.document(title="report.pdf", file_type="pdf", raw=b"%PDF", public=True)
        self.assertEqual(self.fetch(self.other, pdf).status_code, 404)

    def test_only_pdf_and_html_are_renditions(self):
        text = self.document(title="notes.txt", file_type="text", raw=b"x")
        self.assertEqual(self.fetch(self.owner, text).status_code, 404)

    def test_an_encrypted_rendition_is_a_404(self):
        sealed = self.document(title="report.pdf", file_type="pdf", raw=b"x", encrypted=True)
        self.assertEqual(self.fetch(self.owner, sealed).status_code, 404)


class DataUriTests(SimpleTestCase):
    """`media.image_bytes_to_data_uri` is what both media doors inline."""

    def test_a_large_jpeg_is_scaled_down_to_the_maximum_side(self):
        uri = media.image_bytes_to_data_uri(_jpeg(size=(2000, 500)), "image/jpeg")
        self.assertEqual(_decoded_size(uri), (media.MAX_DIM, 160))

    def test_a_small_image_keeps_its_size(self):
        uri = media.image_bytes_to_data_uri(_jpeg(size=(100, 50)), "image/jpeg")
        self.assertEqual(_decoded_size(uri), (100, 50))

    def test_the_real_format_decides_png_or_jpeg_not_the_callers_label(self):
        uri = media.image_bytes_to_data_uri(_png(mode="RGBA"), "image/webp")
        # The bytes are a PNG, so the format sniff keeps it PNG.
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        from PIL import Image

        buf = io.BytesIO()
        Image.new("LA", (4, 4)).save(buf, format="TIFF")
        tiff = media.image_bytes_to_data_uri(buf.getvalue(), "image/tiff")
        self.assertTrue(tiff.startswith("data:image/jpeg;base64,"))

    def test_bytes_that_are_not_an_image_are_embedded_verbatim(self):
        uri = media.image_bytes_to_data_uri(b"not an image", "image/png")
        self.assertEqual(uri, "data:image/png;base64," + base64.b64encode(b"not an image").decode())
        self.assertTrue(media.image_bytes_to_data_uri(b"??", "").startswith(
            "data:application/octet-stream;base64,"))
