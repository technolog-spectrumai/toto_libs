"""Which bytes a family is allowed to run.

The attack: a runner image quietly replaced between being built and being run
would execute somebody else's code inside every Gear on the host, with the
platform's own hardening applied to it and nothing in the logs to say anything
changed.

The counter-risk, and the reason half of these tests exist: a pin that refused
ordinary locally-built images would break every deployment to defend against an
exposure those deployments do not have. Both directions are asserted.
"""

from __future__ import annotations

import json
import os
import tempfile

from django.test import SimpleTestCase

from toto.anastasia.executor import images


class FakeDriver:
    name = "fake"

    def __init__(self, digest="sha256:aaa"):
        self._digest = digest

    def image_digest(self, image):
        if self._digest is RuntimeError:
            raise RuntimeError("the daemon is gone")
        return self._digest


def _lock_file(payload) -> str:
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    if isinstance(payload, str):
        handle.write(payload)
    else:
        json.dump(payload, handle)
    handle.close()
    return handle.name


class LockLoadingTests(SimpleTestCase):
    def test_no_lock_file_means_unpinned(self):
        """Every host today. A missing file must not be an error, or turning
        the feature on would be a flag day for the whole fleet."""
        self.assertEqual(images.load("/definitely/not/here.json"), {})

    def test_a_corrupt_lock_is_refused_rather_than_ignored(self):
        """THE ONE THAT MATTERS. Reading an unparseable file as "no lock"
        would make CORRUPTING it the attack: overwrite the lock with garbage
        and every image becomes acceptable again."""
        path = _lock_file("{not json at all")
        self.addCleanup(os.unlink, path)
        with self.assertRaises(images.ImageMismatch) as caught:
            images.load(path)
        self.assertIn("could not be read", str(caught.exception))

    def test_both_lock_shapes_are_read(self):
        """`{"pdf": "sha256:x"}` and the richer form build.sh writes."""
        path = _lock_file({"pdf": "sha256:x",
                           "latex": {"ref": "anastasia-latex:latest",
                                     "digest": "sha256:y"}})
        self.addCleanup(os.unlink, path)
        self.assertEqual(images.load(path),
                         {"pdf": "sha256:x", "latex": "sha256:y"})


class VerifyTests(SimpleTestCase):
    def test_a_family_the_lock_does_not_name_is_allowed(self):
        """Unpinned is the deliberate default: a locally-built image carries
        no repo digest, and that is every runner image on a host that builds
        its own."""
        images.verify(FakeDriver(), "pdf", "anastasia-pdf", lock={})

    def test_a_matching_digest_is_allowed(self):
        images.verify(FakeDriver("sha256:aaa"), "pdf", "anastasia-pdf",
                      lock={"pdf": "sha256:aaa"})

    def test_a_swapped_image_is_refused(self):
        with self.assertRaises(images.ImageMismatch) as caught:
            images.verify(FakeDriver("sha256:evil"), "pdf", "anastasia-pdf",
                          lock={"pdf": "sha256:good"})
        message = str(caught.exception)
        self.assertIn("not the one this deployment was built with", message)
        # The user-facing half must say nothing ran. A refusal that left
        # somebody wondering whether their job executed is worse than useless.
        self.assertIn("Nothing has been run", message)

    def test_a_pinned_family_whose_image_has_no_digest_is_refused(self):
        """The lock NAMES this family, so silence is not an answer."""
        with self.assertRaises(images.ImageMismatch):
            images.verify(FakeDriver(""), "pdf", "anastasia-pdf",
                          lock={"pdf": "sha256:good"})

    def test_an_unreadable_digest_is_refused_not_waved_through(self):
        """A probe that cannot answer blocks; it does not wave through. Same
        rule the pressure report follows."""
        with self.assertRaises(images.ImageMismatch):
            images.verify(FakeDriver(RuntimeError), "pdf", "anastasia-pdf",
                          lock={"pdf": "sha256:good"})

    def test_an_unreadable_digest_on_an_UNPINNED_family_is_fine(self):
        """Nothing was claimed about it, so nothing is checked — and the
        driver is never even asked."""
        images.verify(FakeDriver(RuntimeError), "pdf", "anastasia-pdf", lock={})
