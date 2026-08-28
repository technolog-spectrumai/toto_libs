"""Size, declared type, actual type — and the antivirus size collision.

The last one is the subtle test in this file: the scanner refuses anything over
its own cap (10 MB by default) BEFORE decoding, and an ordinary scanned book is
about 19 MB. Handing a book straight to `scanning.scan` would refuse precisely
the files this feature exists for.
"""

from __future__ import annotations

import io
import shutil
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from toto.ocr import validation

HAVE_POPPLER = shutil.which("pdfinfo") is not None

CAP = 64 * 1024 * 1024
PAGES = 400


def png_bytes(width=40, height=20) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("L", (width, height), 255).save(buf, format="PNG")
    return buf.getvalue()


def pdf_bytes(pages=1) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    page = Image.new("L", (200, 300), 255)
    page.save(buf, format="PDF", save_all=True,
              append_images=[page] * (pages - 1))
    return buf.getvalue()


def upload(name, data, content_type=""):
    return SimpleUploadedFile(name, data, content_type=content_type)


class SizeTests(TestCase):
    def test_too_large_is_refused_before_anything_is_decoded(self):
        """The antivirus engine's rule: "too big to check" must be a refusal,
        not a silent skip that looks like a pass."""
        big = upload("huge.png", b"x" * 2048, "image/png")
        with mock.patch.object(validation, "_inspect_image") as decode:
            result = validation.inspect_upload(big, max_bytes=1024,
                                               page_cap=PAGES)
            decode.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "too-large")

    def test_the_message_names_both_numbers(self):
        big = upload("huge.png", b"x" * (3 * 1024 * 1024), "image/png")
        result = validation.inspect_upload(big, max_bytes=1024 * 1024,
                                           page_cap=PAGES)
        self.assertIn("3 MB", result.message)
        self.assertIn("1 MB", result.message)

    def test_an_empty_file_is_refused(self):
        result = validation.inspect_upload(upload("x.png", b"", "image/png"),
                                           max_bytes=CAP, page_cap=PAGES)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "empty")


class DeclaredTypeTests(TestCase):
    def test_a_type_with_no_pages_is_refused_by_name(self):
        result = validation.inspect_upload(
            upload("notes.txt", b"hello", "text/plain"),
            max_bytes=CAP, page_cap=PAGES)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "unsupported")
        self.assertIn("text", result.message)


class ActualTypeTests(TestCase):
    def test_a_real_image_is_accepted_as_one_page(self):
        result = validation.inspect_upload(
            upload("scan.png", png_bytes(), "image/png"),
            max_bytes=CAP, page_cap=PAGES)
        self.assertTrue(result.ok, result.message)
        self.assertEqual((result.kind, result.page_count), ("image", 1))

    def test_a_file_that_lies_about_being_an_image_is_refused(self):
        """A name is not evidence. The agreement between what a file claims and
        what it is IS the check."""
        result = validation.inspect_upload(
            upload("scan.png", b"%PDF-1.4 not really a png", "image/png"),
            max_bytes=CAP, page_cap=PAGES)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "wrong-shape")

    def test_a_truncated_image_is_refused(self):
        half = png_bytes()[: 20]
        result = validation.inspect_upload(upload("scan.png", half, "image/png"),
                                           max_bytes=CAP, page_cap=PAGES)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "wrong-shape")


class PdfTests(TestCase):
    def setUp(self):
        if not HAVE_POPPLER:
            self.skipTest("poppler is not installed here")

    def test_a_real_pdf_reports_its_page_count(self):
        result = validation.inspect_upload(
            upload("book.pdf", pdf_bytes(pages=5), "application/pdf"),
            max_bytes=CAP, page_cap=PAGES)
        self.assertTrue(result.ok, result.message)
        self.assertEqual((result.kind, result.page_count), ("pdf", 5))

    def test_a_pdf_that_is_not_a_pdf_is_refused(self):
        result = validation.inspect_upload(
            upload("book.pdf", b"this is not a pdf at all", "application/pdf"),
            max_bytes=CAP, page_cap=PAGES)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "wrong-shape")

    def test_too_many_pages_is_refused_with_both_numbers(self):
        result = validation.inspect_upload(
            upload("book.pdf", pdf_bytes(pages=5), "application/pdf"),
            max_bytes=CAP, page_cap=3)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "too-many-pages")
        self.assertIn("5", result.message)
        self.assertIn("3", result.message)


class ScannerSizeCollisionTests(TestCase):
    """The book-sized hole, and the accessor that closes it."""

    def setUp(self):
        if not HAVE_POPPLER:
            self.skipTest("poppler is not installed here")

    def test_a_file_over_the_scanners_cap_is_passed_through_unscanned(self):
        """And SAYS so. "Checked and clean" and "never checked" must not look
        the same to whoever reads the page."""
        from toto.vault import scanning

        with mock.patch.object(scanning, "should_scan", return_value=True), \
             mock.patch.object(scanning, "scan_size_cap_bytes", return_value=8), \
             mock.patch.object(scanning, "scan") as scan:
            result = validation.inspect_upload(
                upload("book.pdf", pdf_bytes(pages=2), "application/pdf"),
                max_bytes=CAP, page_cap=PAGES)
            scan.assert_not_called()
        self.assertTrue(result.ok)
        self.assertFalse(result.scanned)
        self.assertIn("not screened", result.scan_note)

    def test_a_file_within_the_cap_is_really_scanned(self):
        from toto.vault import scanning

        verdict = scanning.Verdict.clean(scanned=True)
        with mock.patch.object(scanning, "should_scan", return_value=True), \
             mock.patch.object(scanning, "scan_size_cap_bytes",
                               return_value=100 * 1024 * 1024), \
             mock.patch.object(scanning, "scan", return_value=verdict) as scan:
            result = validation.inspect_upload(
                upload("book.pdf", pdf_bytes(pages=2), "application/pdf"),
                max_bytes=CAP, page_cap=PAGES)
            scan.assert_called_once()
        self.assertTrue(result.ok)
        self.assertTrue(result.scanned)

    def test_a_refusal_within_the_cap_is_still_a_hard_refusal(self):
        from toto.vault import scanning

        verdict = scanning.Verdict.refused("active-content", "/JavaScript")
        with mock.patch.object(scanning, "should_scan", return_value=True), \
             mock.patch.object(scanning, "scan_size_cap_bytes",
                               return_value=100 * 1024 * 1024), \
             mock.patch.object(scanning, "scan", return_value=verdict):
            result = validation.inspect_upload(
                upload("book.pdf", pdf_bytes(pages=2), "application/pdf"),
                max_bytes=CAP, page_cap=PAGES)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "active-content")
