"""Language discovery, and the two subprocess wrappers.

The binaries are mocked throughout except where a test explicitly skips without
them: this suite has to pass on a machine with no tesseract, which is exactly
the machine the refusal path exists for.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from unittest import mock

from django.test import SimpleTestCase

from toto.ocr import engine

HAVE_TESSERACT = shutil.which("tesseract") is not None
HAVE_POPPLER = shutil.which("pdftoppm") is not None


class LanguageDiscoveryTests(SimpleTestCase):
    def setUp(self):
        engine._reset_caches_for_tests()

    def tearDown(self):
        engine._reset_caches_for_tests()

    def _listing(self, text):
        return mock.patch.object(
            engine.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0, stdout=text, stderr=""))

    def test_the_header_line_is_not_a_language(self):
        with mock.patch.object(engine, "have", return_value=True), \
             self._listing("List of available languages (3):\neng\npol\ndeu\n"):
            self.assertEqual(engine.available_languages(), ["eng", "deu", "pol"])

    def test_osd_is_not_offered(self):
        """Orientation/script detection ships with tesseract and is not a
        language; choosing it produces empty text that reads like a bug."""
        with mock.patch.object(engine, "have", return_value=True), \
             self._listing("List of available languages (2):\neng\nosd\n"):
            self.assertEqual(engine.available_languages(), ["eng"])

    def test_english_comes_first(self):
        with mock.patch.object(engine, "have", return_value=True), \
             self._listing("Langs:\npol\ndeu\neng\n"):
            self.assertEqual(engine.available_languages()[0], "eng")

    def test_a_missing_binary_falls_back_and_never_raises(self):
        with mock.patch.object(engine, "have", return_value=False):
            self.assertEqual(engine.available_languages(), ["eng"])
            self.assertFalse(engine.tesseract_available())

    def test_the_environment_is_only_a_fallback(self):
        with mock.patch.object(engine, "have", return_value=False), \
             mock.patch.dict("os.environ", {"TESSERACT_LANGS": "eng pol"}):
            self.assertEqual(engine.available_languages(), ["eng", "pol"])

    def test_a_label_falls_back_to_the_bare_code(self):
        self.assertEqual(engine.language_label("pol"), "Polish (pol)")
        self.assertEqual(engine.language_label("zzz"), "zzz")


class SafeLangTests(SimpleTestCase):
    def test_it_accepts_what_tesseract_accepts(self):
        for good in ("eng", "pol", "eng+pol", "eng+pol+deu"):
            self.assertTrue(engine.SAFE_LANG.match(good), good)

    def test_it_refuses_anything_that_could_reach_a_shell(self):
        for bad in ("../../etc/passwd", "eng;rm -rf /", "en", "ENG", "",
                    "eng pol", "eng&&whoami"):
            self.assertFalse(engine.SAFE_LANG.match(bad), bad)

    def test_reading_refuses_a_bad_language_before_running_anything(self):
        with mock.patch.object(engine, "have", return_value=True), \
             mock.patch.object(engine.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                engine.read_image("/tmp/x.png", "eng; rm -rf /")
            run.assert_not_called()

    def test_reading_without_tesseract_names_the_flag(self):
        with mock.patch.object(engine, "have", return_value=False):
            with self.assertRaises(engine.EngineUnavailable) as caught:
                engine.read_image("/tmp/x.png", "eng")
            self.assertIn("BUILD_OCR", str(caught.exception))


class RealBinaryTests(SimpleTestCase):
    """Against the actual programs, where the machine has them."""

    def setUp(self):
        if not (HAVE_TESSERACT and HAVE_POPPLER):
            self.skipTest("tesseract and poppler are not installed here")
        engine._reset_caches_for_tests()

    def _book(self, pages: int, path: str):
        from PIL import Image, ImageDraw

        page = Image.new("L", (1240, 1754), 255)
        draw = ImageDraw.Draw(page)
        for i in range(20):
            draw.text((120, 200 + i * 60), "The quick brown fox jumps over it.",
                      fill=0)
        page.save(path, save_all=True, append_images=[page] * (pages - 1))

    def test_counting_pages_does_not_rasterise_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/book.pdf"
            self._book(12, path)
            self.assertEqual(engine.page_count(path), 12)

    def test_one_page_is_rendered_without_the_rest(self):
        """`-f N -l N` is what keeps a 300-page book affordable: extracting one
        page must cost one page's work, not the document's."""
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/book.pdf"
            self._book(12, path)
            out = engine.render_pdf_page(path, 7, tmp)
            self.assertTrue(out.endswith(".png"))
            import os
            self.assertGreater(os.path.getsize(out), 0)
            # Exactly one image, not twelve.
            self.assertEqual(
                len([f for f in os.listdir(tmp) if f.endswith(".png")]), 1)

    def test_a_page_of_words_reads_back_as_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/book.pdf"
            self._book(1, path)
            image = engine.render_pdf_page(path, 1, tmp)
            text = engine.read_image(image, "eng")
            self.assertIn("quick", text.lower())

    def test_asking_for_a_page_that_is_not_there_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/book.pdf"
            self._book(2, path)
            with self.assertRaises(ValueError):
                engine.render_pdf_page(path, 9, tmp)
