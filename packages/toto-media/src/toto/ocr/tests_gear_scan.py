"""Where an OCR scan happens, and what changed by moving it.

This is the path that closed the worst hole in the old media tier. OCR ran
tesseract **synchronously inside a POST handler**, on bytes a user had just
uploaded, with no run row, no metric, no timeout and no memory bound. It has
all four now, because it is a job in a container like every other.

Still synchronous from the browser's side, deliberately: a scan is seconds, and
turning it into poll-and-wait would change a working interaction for nothing.
"""

from __future__ import annotations

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from toto.ocr import ocr as ocr_mod

POOL = {"cpu_millicores": 4000, "ram_mb": 8192, "scratch_mb": 8192,
        "pids": 2048}


class GearSelectionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("scanner", password="x")

    def test_no_anastasia_means_scan_here(self):
        """A host without Compute Gears keeps its old behaviour exactly."""
        with mock.patch("django.apps.apps.is_installed", return_value=False):
            self.assertIsNone(ocr_mod.gear_for(self.user))

    @override_settings(ANASTASIA_POOL=POOL)
    def test_a_mounted_gear_is_used(self):
        from toto.anastasia import services
        from toto.anastasia.limits import Limits

        with override_settings(ANASTASIA_RUNTIME_BACKEND=
                               "toto.anastasia.tests.base.FakeRuntimeBackend"):
            lease = services.reserve(owner=self.user, name="ocr",
                                     limits=Limits(1000, 1024, 512, 128))
            services.mount(lease=lease, actor=self.user)
            self.assertEqual(ocr_mod.gear_for(self.user).pk, lease.pk)

    @override_settings(ANASTASIA_POOL=POOL)
    def test_with_no_gear_it_falls_back_only_where_tesseract_exists(self):
        """The asymmetry that keeps both worlds working.

        A host that still has the binary scans here. Zenobia does not, so the
        refusal is the honest answer — and it names something the user can act
        on rather than 500ing.
        """
        from toto.anastasia import jobs

        with mock.patch("shutil.which", return_value="/usr/bin/tesseract"):
            self.assertIsNone(ocr_mod.gear_for(self.user))

        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(jobs.NoGear):
                ocr_mod.gear_for(self.user)


class ScanShapeTests(TestCase):
    """The runner returns what the page already renders."""

    def test_lines_come_back_in_the_helpers_shape(self):
        lines = [{"text": "HELLO", "left": 1, "top": 2, "width": 3,
                  "height": 4, "confidence": 90}]
        payload = json.dumps({"lines": lines, "text": "HELLO"}).encode()

        with mock.patch("toto.anastasia.jobs.run",
                        return_value={"outputs": {"output.json": payload},
                                      "report": {}, "execution": None}):
            got = ocr_mod.scan_in_gear(b"png-bytes", filename="shot.png",
                                       language="eng", lease=object(),
                                       user=None)
        self.assertEqual(got, lines)

    def test_a_runner_that_produced_nothing_is_an_error_not_an_empty_scan(self):
        """An empty result would read as "this image has no text", which is a
        different and much more confusing answer than "the scan failed"."""
        with mock.patch("toto.anastasia.jobs.run",
                        return_value={"outputs": {}, "report": {},
                                      "execution": None}):
            with self.assertRaises(RuntimeError):
                ocr_mod.scan_in_gear(b"x", filename="a.png", language="eng",
                                     lease=object(), user=None)

    def test_the_staged_name_keeps_the_extension(self):
        """tesseract and PIL both go by extension; a .png staged as .bin is a
        scan that fails for a reason nobody can see."""
        seen = {}

        def capture(**kwargs):
            seen.update(kwargs)
            return {"outputs": {"output.json": b'{"lines": []}'}, "report": {},
                    "execution": None}

        with mock.patch("toto.anastasia.jobs.run", side_effect=capture):
            ocr_mod.scan_in_gear(b"x", filename="Screenshot.JPG",
                                 language="eng", lease=object(), user=None)
        self.assertEqual(list(seen["inputs"]), ["input.jpg"])
        self.assertEqual(seen["params"]["input"], "input.jpg")
