"""Incremental log reads: the half a progress console needs.

Unit-level against a stubbed `_run`. The executor stays request/response, so
what is tested here is the arithmetic of offsets — which is where a console
either repeats lines, drops them, or loops for ever.
"""

from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase

from toto.anastasia.executor.drivers import docker as docker_driver


def _driver(output: str):
    driver = docker_driver.DockerClient()
    driver._run = lambda args, **kw: SimpleNamespace(
        returncode=0, stdout=output, stderr="")
    return driver


class LogsSinceTests(SimpleTestCase):
    def test_the_first_read_starts_at_the_beginning(self):
        out = _driver("hello\n").logs_since("c")
        self.assertEqual(out["text"], "hello\n")
        self.assertEqual(out["offset"], 6)
        self.assertTrue(out["complete"])

    def test_a_second_read_returns_only_what_is_new(self):
        """The whole point. A console that re-rendered the log each poll would
        duplicate every line it had already shown."""
        driver = _driver("one\ntwo\n")
        first = driver.logs_since("c", 0)
        self.assertEqual(first["text"], "one\ntwo\n")
        second = driver.logs_since("c", first["offset"])
        self.assertEqual(second["text"], "")
        self.assertEqual(second["offset"], first["offset"])

    def test_growth_between_reads_is_picked_up(self):
        driver = _driver("one\n")
        first = driver.logs_since("c", 0)
        driver._run = lambda args, **kw: SimpleNamespace(
            returncode=0, stdout="one\ntwo\n", stderr="")
        second = driver.logs_since("c", first["offset"])
        self.assertEqual(second["text"], "two\n")

    def test_a_long_log_is_sliced_and_says_it_is_incomplete(self):
        """A console renders what it is handed. A 200 MB build log in one
        response is a denial of service against the thing asking for
        progress."""
        driver = _driver("x" * (docker_driver.DockerClient.LOG_SLICE_BYTES * 2))
        out = driver.logs_since("c", 0)
        self.assertEqual(len(out["text"]),
                         docker_driver.DockerClient.LOG_SLICE_BYTES)
        self.assertFalse(out["complete"],
                         "a truncated slice must tell the caller to ask again")

    def test_an_offset_past_the_end_restarts_rather_than_stalling(self):
        """The log got SHORTER than the caller's offset — a container was
        recreated, or docker rotated it. Clamping to the end would return
        nothing for ever, which reads as a hung job."""
        out = _driver("short\n").logs_since("c", 10_000)
        self.assertEqual(out["text"], "short\n")
        self.assertEqual(out["offset"], 6)

    def test_a_negative_or_junk_offset_is_treated_as_zero(self):
        for bad in (-5, None, "", "abc"):
            with self.subTest(offset=bad):
                try:
                    out = _driver("hi\n").logs_since("c", bad)
                except (TypeError, ValueError):
                    self.fail(f"offset {bad!r} should be tolerated, not raise")
                self.assertEqual(out["text"], "hi\n")

    def test_a_multibyte_character_split_by_a_slice_does_not_raise(self):
        """A slice can cut a UTF-8 character in half, and a console that
        raises on that shows the user nothing at all.

        ONE ASCII BYTE FIRST. `LOG_SLICE_BYTES` is even and `é` is two bytes,
        so a log of nothing but `é` slices cleanly at every boundary and the
        earlier form of this test never split a character at all — it passed
        with `errors="strict"`. The leading byte shifts the boundary into the
        middle of one, which is the case the driver's `replace` exists for.
        """
        slice_bytes = docker_driver.DockerClient.LOG_SLICE_BYTES
        driver = _driver("x" + "é" * slice_bytes)
        out = driver.logs_since("c", 0)
        self.assertIsInstance(out["text"], str)
        # The half character is replaced, not raised on, and not silently
        # dropped: U+FFFD is the honest rendering of a byte that is not a
        # character yet.
        self.assertTrue(out["text"].endswith("\ufffd"))
        self.assertEqual(out["offset"], slice_bytes)
        self.assertFalse(out["complete"])

    def test_stderr_is_included_because_that_is_where_failures_go(self):
        driver = docker_driver.DockerClient()
        driver._run = lambda args, **kw: SimpleNamespace(
            returncode=0, stdout="out\n", stderr="err\n")
        self.assertIn("err", driver.logs_since("c", 0)["text"])
