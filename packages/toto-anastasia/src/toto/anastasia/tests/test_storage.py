"""Capsule storage statistics: counts, and the promise that it is only counts.

Unit-level and fast — a temp tree, not a capsule. What is worth testing here is
not arithmetic but the boundary: that nothing leaks a name, that a partial walk
says so, and that a symlink cannot smuggle the host's size into a capsule's.
"""

from __future__ import annotations

import os
import tempfile

from django.test import SimpleTestCase

from toto.anastasia.executor import storage


def _tree(root, spec):
    for name, payload in spec.items():
        path = os.path.join(root, name)
        if isinstance(payload, dict):
            os.makedirs(path, exist_ok=True)
            _tree(path, payload)
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(payload)


class MeasureTests(SimpleTestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="anastasia-storage-")
        self.addCleanup(__import__("shutil").rmtree, self.root,
                        ignore_errors=True)

    def test_it_counts_bytes_and_files(self):
        _tree(self.root, {"a.txt": b"x" * 100,
                          "sub": {"b.bin": b"y" * 250}})
        out = storage.measure(self.root)
        self.assertEqual(out["bytes"], 350)
        self.assertEqual(out["files"], 2)
        self.assertEqual(out["directories"], 1)
        self.assertTrue(out["complete"])

    def test_it_returns_ONLY_numbers(self):
        """THE WHOLE DESIGN, asserted rather than trusted.

        An operator may know how much disk a capsule uses and must not learn
        what is in it. If a filename ever appears in this dict, someone has
        turned an accounting endpoint into a listing.
        """
        _tree(self.root, {"secret-thesis-draft.txt": b"private"})
        out = storage.measure(self.root)
        for key, value in out.items():
            with self.subTest(key=key):
                self.assertIsInstance(value, (int, float, bool),
                                      f"{key} is not a number")
        self.assertNotIn("secret-thesis-draft.txt", repr(out))

    def test_a_missing_directory_is_zero_not_an_error(self):
        """Asked about a capsule that was never mounted, or one already
        destroyed. Raising would make the caller special-case the ordinary."""
        out = storage.measure(os.path.join(self.root, "nope"))
        self.assertEqual(out["bytes"], 0)
        self.assertTrue(out["complete"])

    def test_a_symlink_is_counted_but_never_followed(self):
        """A link into the host would otherwise report the host's bytes as the
        capsule's — and following it is a read, which this module does not do."""
        _tree(self.root, {"real.txt": b"z" * 10})
        os.symlink("/etc", os.path.join(self.root, "escape"))
        out = storage.measure(self.root)
        self.assertEqual(out["files"], 1)
        self.assertEqual(out["symlinks"], 1)
        self.assertEqual(out["bytes"], 10)

    def test_a_truncated_walk_says_so(self):
        """A number that silently stopped early reads as "this capsule is
        small", which is the opposite of the truth and the reading an operator
        would act on."""
        _tree(self.root, {f"f{i}.txt": b"." for i in range(20)})
        out = storage.measure(self.root, max_entries=5)
        self.assertFalse(out["complete"])

    def test_a_time_budget_also_truncates_honestly(self):
        _tree(self.root, {f"f{i}.txt": b"." for i in range(20)})
        out = storage.measure(self.root, budget_seconds=0.0)
        self.assertFalse(out["complete"])

    def test_by_area_takes_its_names_from_the_caller(self):
        """Names come from the caller, never from the filesystem — that is
        what stops this becoming a listing by another route. An area that does
        not exist reports zero rather than being discovered."""
        _tree(self.root, {"in": {"a": b"1234"}, "out": {"b": b"12"}})
        out = storage.by_area(self.root, ("in", "out", "scratch"))
        self.assertEqual(out["in"]["bytes"], 4)
        self.assertEqual(out["out"]["bytes"], 2)
        self.assertEqual(out["scratch"]["bytes"], 0)
        self.assertEqual(sorted(out), ["in", "out", "scratch"])

    def test_it_is_django_free(self):
        """The executor half imports with no Django, and this runs inside it."""
        import subprocess
        import sys

        probe = subprocess.run(
            [sys.executable, "-c",
             "import sys; import toto.anastasia.executor.storage as m; "
             "assert 'django' not in sys.modules, sorted(sys.modules)[:5]; "
             "print('clean')"],
            capture_output=True, text=True)
        self.assertIn("clean", probe.stdout, probe.stderr)
