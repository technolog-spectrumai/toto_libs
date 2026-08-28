"""Pages run independently, recombine in order, and account for themselves once.

Everything here runs the page task in-process with the two binaries mocked, so
the properties under test are the ones this design is actually responsible for:
ordering, partial failure, idempotency, cancellation and cleanup — not whether
Tesseract can read.
"""

from __future__ import annotations

import io
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from toto.ocr import runs
from toto.ocr.models import (OcrPage, OcrRun, OcrSettings, PageStatus,
                             RunStatus)
from toto.ocr.tasks import ocr_page
from toto.ocr.validation import Inspection

MEDIA = tempfile.mkdtemp(prefix="ocr-tests-")


def png_bytes():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("L", (20, 20), 255).save(buf, format="PNG")
    return buf.getvalue()


@override_settings(MEDIA_ROOT=MEDIA)
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("reader", password="x")
        cls.other = User.objects.create_user("stranger", password="x")

    def make_run(self, pages=3, kind="pdf", owner=None):
        upload = SimpleUploadedFile("book.pdf", png_bytes(),
                                    content_type="application/pdf")
        inspection = Inspection(ok=True, kind=kind, page_count=pages,
                               scanned=True)
        return runs.create_run(owner=owner or self.user, inspection=inspection,
                               language="eng", source_name="book.pdf",
                               uploaded=upload)

    def read_as(self, mapping):
        """Patch the engine so page N returns mapping[N], or raises it."""
        def _render(path, number, out_dir):
            return f"{out_dir}/page-{number}.png"

        def _read(image_path, language):
            number = int(image_path.rsplit("-", 1)[1].split(".")[0])
            value = mapping[number]
            if isinstance(value, Exception):
                raise value
            return value

        from toto.ocr import engine
        return (mock.patch.object(engine, "render_pdf_page", _render),
                mock.patch.object(engine, "read_image", _read))


class PageOrderTests(_Fixture):
    def test_the_denominator_is_frozen_and_one_row_exists_per_page(self):
        run = self.make_run(pages=4)
        self.assertEqual(run.total_pages, 4)
        self.assertEqual(
            list(run.pages.order_by("number").values_list("number", flat=True)),
            [1, 2, 3, 4])

    def test_text_comes_back_in_page_order_whatever_the_finish_order(self):
        """Order is data, not arrival. This runs the pages backwards."""
        run = self.make_run(pages=3)
        render, read = self.read_as({1: "first", 2: "second", 3: "third"})
        with render, read:
            for number in (3, 1, 2):
                ocr_page(run.pk, number)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.SUCCESS)
        self.assertLess(run.text.index("first"), run.text.index("second"))
        self.assertLess(run.text.index("second"), run.text.index("third"))

    def test_a_single_page_is_not_labelled(self):
        run = self.make_run(pages=1)
        render, read = self.read_as({1: "just this"})
        with render, read:
            ocr_page(run.pk, 1)
        run.refresh_from_db()
        self.assertEqual(run.text, "just this")


class PartialFailureTests(_Fixture):
    def test_one_bad_page_does_not_discard_the_others(self):
        run = self.make_run(pages=3)
        render, read = self.read_as(
            {1: "good one", 2: ValueError("unreadable"), 3: "good three"})
        with render, read:
            for number in (1, 2, 3):
                ocr_page(run.pk, number)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.PARTIAL)
        self.assertEqual((run.pages_done, run.pages_failed), (2, 1))
        self.assertIn("good one", run.text)
        self.assertIn("good three", run.text)

    def test_the_missing_page_leaves_a_marker_not_a_hole(self):
        """A result silently missing page 2 is a corrupted document presented
        as a good one, and the reader has no way to know."""
        run = self.make_run(pages=3)
        render, read = self.read_as(
            {1: "a", 2: ValueError("unreadable"), 3: "c"})
        with render, read:
            for number in (1, 2, 3):
                ocr_page(run.pk, number)
        run.refresh_from_db()
        self.assertIn("page 2 could not be read", run.text)

    def test_the_reason_is_recorded_per_page(self):
        run = self.make_run(pages=2)
        render, read = self.read_as({1: "a", 2: ValueError("no glyphs here")})
        with render, read:
            for number in (1, 2):
                ocr_page(run.pk, number)
        run.refresh_from_db()
        self.assertEqual(len(run.page_errors), 1)
        self.assertEqual(run.page_errors[0]["page"], 2)
        self.assertIn("no glyphs", run.page_errors[0]["reason"])

    def test_every_page_failing_is_a_failed_run(self):
        run = self.make_run(pages=2)
        render, read = self.read_as(
            {1: ValueError("x"), 2: ValueError("y")})
        with render, read:
            for number in (1, 2):
                ocr_page(run.pk, number)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.FAILED)


class IdempotencyTests(_Fixture):
    def test_a_redelivered_task_does_not_count_twice(self):
        """Celery can deliver the same task again. The page's own status is the
        guard: a second delivery finds it settled and does nothing."""
        run = self.make_run(pages=2)
        render, read = self.read_as({1: "a", 2: "b"})
        with render, read:
            ocr_page(run.pk, 1)
            ocr_page(run.pk, 1)
            ocr_page(run.pk, 2)
        run.refresh_from_db()
        self.assertEqual(run.pages_settled, 2)
        self.assertEqual(run.pages_done, 2)
        self.assertEqual(run.status, RunStatus.SUCCESS)

    def test_only_one_page_finalises_the_run(self):
        run = self.make_run(pages=2)
        render, read = self.read_as({1: "a", 2: "b"})
        with render, read, mock.patch.object(runs, "finalise",
                                             wraps=runs.finalise) as fin:
            ocr_page(run.pk, 1)
            ocr_page(run.pk, 2)
            self.assertEqual(fin.call_count, 1)


class CleanupTests(_Fixture):
    def test_a_successful_run_gives_up_its_source_at_once(self):
        run = self.make_run(pages=1)
        self.assertTrue(run.source.name)
        path = run.source.path
        render, read = self.read_as({1: "done"})
        with render, read:
            ocr_page(run.pk, 1)
        run.refresh_from_db()
        import os
        self.assertFalse(run.source.name)
        self.assertFalse(os.path.exists(path))

    def test_a_partial_run_keeps_its_source_so_it_can_be_retried(self):
        run = self.make_run(pages=2)
        render, read = self.read_as({1: "ok", 2: ValueError("bad")})
        with render, read:
            for number in (1, 2):
                ocr_page(run.pk, number)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.PARTIAL)
        self.assertTrue(run.source.name)
        import os
        self.assertTrue(os.path.exists(run.source.path))

    def test_the_sweep_removes_the_row_and_the_bytes(self):
        """Deleting a row does NOT delete a FileField's blob."""
        from django.utils import timezone

        from toto.ocr.tasks import ocr_cleanup

        run = self.make_run(pages=1)
        path = run.source.path
        OcrRun.objects.filter(pk=run.pk).update(
            status=RunStatus.SUCCESS,
            finished_at=timezone.now() - timezone.timedelta(days=30))
        ocr_cleanup()
        import os
        self.assertFalse(OcrRun.objects.filter(pk=run.pk).exists())
        self.assertFalse(os.path.exists(path))

    def test_a_fresh_run_survives_the_sweep(self):
        from django.utils import timezone

        from toto.ocr.tasks import ocr_cleanup

        run = self.make_run(pages=1)
        OcrRun.objects.filter(pk=run.pk).update(
            status=RunStatus.SUCCESS,
            finished_at=timezone.now() - timezone.timedelta(days=1))
        ocr_cleanup()
        self.assertTrue(OcrRun.objects.filter(pk=run.pk).exists())

    def test_retention_off_removes_nothing(self):
        from django.utils import timezone

        from toto.ocr.tasks import ocr_cleanup

        settings_row = OcrSettings.get()
        settings_row.retention_enabled = False
        settings_row.save()
        run = self.make_run(pages=1)
        OcrRun.objects.filter(pk=run.pk).update(
            status=RunStatus.SUCCESS,
            finished_at=timezone.now() - timezone.timedelta(days=99))
        self.assertEqual(ocr_cleanup(), "disabled")
        self.assertTrue(OcrRun.objects.filter(pk=run.pk).exists())

    def test_the_boundary_is_the_one_derivation(self):
        """The sweep must not compute its own cutoff."""
        from toto.ocr import tasks

        with mock.patch.object(OcrSettings, "boundary", return_value=None) as b:
            self.assertEqual(tasks.ocr_cleanup(), "disabled")
            b.assert_called_once()


class CancellationTests(_Fixture):
    def test_cancelling_stops_what_has_not_started(self):
        from toto.ocr import dispatch

        run = self.make_run(pages=3)
        dispatch.cancel_run(run)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.CANCELLED)
        self.assertEqual(
            run.pages.filter(status=PageStatus.CANCELLED).count(), 3)

    def test_a_task_that_starts_after_a_cancel_does_no_work(self):
        from toto.ocr import dispatch, engine

        run = self.make_run(pages=2)
        dispatch.cancel_run(run)
        with mock.patch.object(engine, "read_image") as read:
            ocr_page(run.pk, 1)
            read.assert_not_called()

    def test_pages_already_read_keep_their_text(self):
        """Discarding work somebody already paid for is the worst possible
        reading of "cancel"."""
        from toto.ocr import dispatch

        run = self.make_run(pages=3)
        render, read = self.read_as({1: "kept", 2: "", 3: ""})
        with render, read:
            ocr_page(run.pk, 1)
        run.refresh_from_db()
        dispatch.cancel_run(run)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.CANCELLED)
        self.assertIn("kept", run.text)


class PayloadTests(_Fixture):
    def test_the_status_counts_are_per_status_not_per_page(self):
        """OcrPage.Meta.ordering is ["number"], and Django folds a model's
        default ordering into GROUP BY — so the counting query needs a trailing
        .order_by() or it returns one row per PAGE. This is that test."""
        run = self.make_run(pages=5)
        render, read = self.read_as({n: "x" for n in range(1, 6)})
        with render, read:
            ocr_page(run.pk, 1)
            ocr_page(run.pk, 2)
        run.refresh_from_db()
        payload = runs.run_payload(run)
        self.assertEqual(payload["pages_done"], 2)
        self.assertEqual(payload["pages_waiting"], 3)
        self.assertEqual(payload["total_pages"], 5)

    def test_percent_is_none_without_a_denominator(self):
        run = self.make_run(pages=2)
        OcrRun.objects.filter(pk=run.pk).update(total_pages=None)
        run.refresh_from_db()
        self.assertIsNone(runs.run_payload(run)["percent"])

    def test_text_is_withheld_until_the_run_finishes(self):
        run = self.make_run(pages=2)
        render, read = self.read_as({1: "half", 2: "done"})
        with render, read:
            ocr_page(run.pk, 1)
        run.refresh_from_db()
        self.assertEqual(runs.run_payload(run)["text"], "")


class ImageGroupTests(_Fixture):
    """Several images submitted as one job — one page each, in order."""

    def group(self, count=3):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return [SimpleUploadedFile(f"page{n}.png", png_bytes(),
                                   content_type="image/png")
                for n in range(1, count + 1)]

    def make_group_run(self, count=3):
        from toto.ocr.validation import Inspection

        return runs.create_run(
            owner=self.user,
            inspection=Inspection(ok=True, kind="image", page_count=count),
            language="eng", source_name=f"page1.png and {count - 1} more",
            group=self.group(count))

    def test_each_image_becomes_one_page_with_its_own_file(self):
        run = self.make_group_run(3)
        self.assertEqual(run.total_pages, 3)
        self.assertEqual(len(run.sources), 3)
        self.assertEqual(run.pages.count(), 3)
        # Every page resolves to a DIFFERENT file.
        paths = {runs.source_path(run, n) for n in (1, 2, 3)}
        self.assertEqual(len(paths), 3)

    def test_a_group_reads_in_the_order_it_was_given(self):
        run = self.make_group_run(3)
        from toto.ocr import engine

        def _read(image_path, language):
            # Each page's own stored file ends in "-N-pageN.png".
            return "text-of-" + image_path.rsplit("-", 1)[1].split(".")[0]

        with mock.patch.object(engine, "read_image", _read):
            for number in (2, 3, 1):
                ocr_page(run.pk, number)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.SUCCESS)
        self.assertLess(run.text.index("page1"), run.text.index("page2"))
        self.assertLess(run.text.index("page2"), run.text.index("page3"))

    def test_a_group_never_rasterises_anything(self):
        """Images are already pages; poppler is for PDFs only."""
        run = self.make_group_run(2)
        from toto.ocr import engine

        with mock.patch.object(engine, "render_pdf_page") as render, \
             mock.patch.object(engine, "read_image", return_value="x"):
            ocr_page(run.pk, 1)
            render.assert_not_called()

    def test_a_finished_group_gives_up_every_file(self):
        import os

        run = self.make_group_run(2)
        paths = [runs.source_path(run, n) for n in (1, 2)]
        from toto.ocr import engine

        with mock.patch.object(engine, "read_image", return_value="done"):
            ocr_page(run.pk, 1)
            ocr_page(run.pk, 2)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.SUCCESS)
        self.assertEqual(run.sources, [])
        for path in paths:
            self.assertFalse(os.path.exists(path), path)


class GroupValidationTests(TestCase):
    """The cap is on the SUM, or it is not a cap."""

    def _images(self, count, size):
        from django.core.files.uploadedfile import SimpleUploadedFile

        real = png_bytes()
        return [SimpleUploadedFile(f"p{n}.png", real + b"\0" * size,
                                   content_type="image/png")
                for n in range(count)]

    def test_many_small_images_cannot_evade_the_size_cap(self):
        from toto.ocr import validation

        result = validation.inspect_group(self._images(8, 200_000),
                                          max_bytes=1_000_000, page_cap=400)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "too-large")

    def test_too_many_images_is_refused_with_both_numbers(self):
        from toto.ocr import validation

        result = validation.inspect_group(self._images(6, 10),
                                          max_bytes=50_000_000, page_cap=4)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "too-many-pages")

    def test_one_bad_image_is_named_now_not_twenty_minutes_later(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from toto.ocr import validation

        good = SimpleUploadedFile("good.png", png_bytes(),
                                  content_type="image/png")
        bad = SimpleUploadedFile("broken.png", b"not an image at all",
                                 content_type="image/png")
        result = validation.inspect_group([good, bad], max_bytes=50_000_000,
                                          page_cap=400)
        self.assertFalse(result.ok)
        self.assertIn("broken.png", result.message)

    def test_a_pdf_cannot_be_mixed_into_a_group_of_images(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from toto.ocr import validation

        good = SimpleUploadedFile("good.png", png_bytes(),
                                  content_type="image/png")
        pdf = SimpleUploadedFile("book.pdf", b"%PDF-1.4",
                                 content_type="application/pdf")
        result = validation.inspect_group([good, pdf], max_bytes=50_000_000,
                                          page_cap=400)
        self.assertFalse(result.ok)
