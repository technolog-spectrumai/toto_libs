"""The files area: what a name may reach, and what an unmount keeps.

Three layers, each asked the same two questions — does a hostile name get
refused, and does the files area outlive the mount:

* `executor/files.py` on a temp tree, with REAL violations planted: a
  traversal, an absolute path, a NUL, a symlink out of the root, a symlinked
  parent. A test that only checks the happy path would pass over a module
  that followed every link.
* `CapsuleManager` over `FakeDocker`, for the lifetime rule and the argv.
* the HTTP surface, over a real socket, for routing and for the one property
  the routes are shaped around: the name is in the signed body, and a query
  string is ignored.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import tempfile
import uuid
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from toto.anastasia.limits import Limits
from toto.anastasia.executor import capsules, files, protocol, reconcile
from toto.anastasia.executor import service, staging
from toto.anastasia import executor_backend
from toto.anastasia.runtime import RuntimeUnavailable

from .fakes import CountingSliceDriver, FakeDocker
from .test_service import SECRET, ServiceTestCase

BUDGET = 1024


def _plant(root, name, data=b"x"):
    path = os.path.join(root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _numbers_only(testcase, value, where="storage"):
    """Every leaf is a number; a filename anywhere is the boundary broken."""
    if isinstance(value, dict):
        for key, inner in value.items():
            _numbers_only(testcase, inner, f"{where}.{key}")
    else:
        testcase.assertIsInstance(value, (int, float, bool),
                                  f"{where} is {value!r}, not a number")


class FilesAreaTests(SimpleTestCase):
    def setUp(self):
        base = tempfile.mkdtemp(prefix="anastasia-files-")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.root = os.path.join(base, "files")
        os.makedirs(self.root)
        # Somewhere OUTSIDE the root, holding a secret a hostile name would
        # like to read and a directory it would like to write into.
        self.outside = os.path.join(base, "outside")
        os.makedirs(self.outside)
        self.secret = _plant(self.outside, "secret.txt", b"host secret")

    def _outside_untouched(self):
        self.assertEqual(sorted(os.listdir(self.outside)), ["secret.txt"])
        with open(self.secret, "rb") as handle:
            self.assertEqual(handle.read(), b"host secret")

    # -- the happy path, once, so the refusals below are not vacuous --------

    def test_a_round_trip(self):
        out = files.write_one(self.root, "docs/a.txt", b"hello", max_bytes=BUDGET)
        self.assertEqual(out, {"name": "docs/a.txt", "size": 5})
        listed = files.listing(self.root)
        self.assertTrue(listed["complete"])
        self.assertEqual([r["name"] for r in listed["files"]], ["docs", "docs/a.txt"])
        row = listed["files"][1]
        self.assertEqual(row["size"], 5)
        self.assertFalse(row["is_dir"])
        self.assertIsInstance(row["modified"], int)
        self.assertEqual(files.read_one(self.root, "docs/a.txt", max_bytes=BUDGET),
                         b"hello")
        self.assertEqual(files.delete_one(self.root, "docs/a.txt"),
                         {"name": "docs/a.txt", "deleted": True})
        self.assertEqual([r["name"] for r in files.listing(self.root)["files"]],
                         ["docs"])

    # -- names ---------------------------------------------------------------

    def test_traversal_is_refused_by_every_verb(self):
        for name in ("../x", "a/../../x", "..", ".", "a/b/../../../x"):
            with self.subTest(name=name):
                with self.assertRaises(files.FilesError) as caught:
                    files.read_one(self.root, name, max_bytes=BUDGET)
                self.assertIn("file", str(caught.exception))
                with self.assertRaises(files.FilesError):
                    files.write_one(self.root, name, b"x", max_bytes=BUDGET,
                                    replace=True)
                with self.assertRaises(files.FilesError):
                    files.delete_one(self.root, name)
        self.assertEqual(os.listdir(self.root), [])
        self._outside_untouched()

    def test_absolute_paths_and_drive_letters_are_refused(self):
        for name in ("/etc/passwd", self.secret, "C:\\x", "c:/x", "\\\\srv\\share"):
            with self.subTest(name=name):
                with self.assertRaises(files.FilesError):
                    files.read_one(self.root, name, max_bytes=BUDGET)
                with self.assertRaises(files.FilesError):
                    files.write_one(self.root, name, b"x", max_bytes=BUDGET,
                                    replace=True)
        self._outside_untouched()

    def test_a_nul_byte_is_refused_before_any_syscall_sees_it(self):
        """A JSON body, unlike a tar, CAN carry one — and `os.open` would raise
        a bare ValueError about it, which is a 400 by accident rather than a
        sentence a person can act on."""
        with self.assertRaises(files.FilesError) as caught:
            files.write_one(self.root, "a\x00b", b"x", max_bytes=BUDGET)
        self.assertIn("NUL", str(caught.exception))

    def test_a_name_must_be_a_string(self):
        for name in (None, 5, ["a"], b"a"):
            with self.subTest(name=name):
                with self.assertRaises(files.FilesError):
                    files.read_one(self.root, name, max_bytes=BUDGET)

    def test_a_refusal_is_one_sentence_a_person_can_act_on(self):
        with self.assertRaises(files.FilesError) as caught:
            files.write_one(self.root, "../x", b"x", max_bytes=BUDGET)
        self.assertEqual(str(caught.exception),
                         "file escapes the files area: '../x'")
        self.assertIsInstance(caught.exception, ValueError)

    def test_the_staging_rule_still_speaks_in_its_own_voice(self):
        """One rule, two sentences. The tar path must not start saying
        "file" — test_service asserts on "escapes" for a hostile payload and
        the archive wording is what a job's error shows."""
        with self.assertRaises(staging.StagingError) as caught:
            staging.safe_member_name("../x")
        self.assertEqual(str(caught.exception),
                         "archive member escapes the staging directory: '../x'")
        self.assertIs(staging._resolved_within, staging.resolved_within)

    # -- symlinks ------------------------------------------------------------

    def test_a_symlink_out_of_the_root_is_refused_by_read_write_and_listing(self):
        """The runner-planted link. Reading it would read the host as root,
        writing through it would write the host, and listing it would offer
        the desk a name it can do nothing with."""
        os.symlink(self.secret, os.path.join(self.root, "escape"))
        _plant(self.root, "real.txt", b"mine")

        with self.assertRaises(files.FilesError) as caught:
            files.read_one(self.root, "escape", max_bytes=BUDGET)
        self.assertIn("symbolic link", str(caught.exception))

        with self.assertRaises(files.FilesError):
            files.write_one(self.root, "escape", b"overwritten", max_bytes=BUDGET,
                            replace=True)
        with self.assertRaises(files.FilesError):
            files.write_one(self.root, "escape", b"overwritten", max_bytes=BUDGET)

        with self.assertRaises(files.FilesError):
            files.delete_one(self.root, "escape")
        self.assertTrue(os.path.islink(os.path.join(self.root, "escape")),
                        "a refused delete must not have unlinked anything")

        names = [r["name"] for r in files.listing(self.root)["files"]]
        self.assertEqual(names, ["real.txt"])
        self._outside_untouched()

    def test_a_symlinked_parent_directory_is_refused(self):
        """`realpath` alone would accept `dirlink/new.txt` when `dirlink`
        points INSIDE the root; a link is refused wherever it stands on the
        path, because a runner can repoint it after the check."""
        os.symlink(self.outside, os.path.join(self.root, "dirlink"))
        inside = os.path.join(self.root, "inside")
        os.makedirs(inside)
        os.symlink(inside, os.path.join(self.root, "innerlink"))

        for name in ("dirlink/secret.txt", "innerlink/x.txt"):
            with self.subTest(name=name):
                with self.assertRaises(files.FilesError) as caught:
                    files.read_one(self.root, name, max_bytes=BUDGET)
                self.assertIn("symbolic link", str(caught.exception))
                with self.assertRaises(files.FilesError):
                    files.write_one(self.root, name, b"x", max_bytes=BUDGET,
                                    replace=True)
                with self.assertRaises(files.FilesError):
                    files.delete_one(self.root, name)
        # Neither side of either link gained a file.
        self.assertEqual(os.listdir(inside), [])
        self._outside_untouched()
        # And the listing did not descend into either.
        names = [r["name"] for r in files.listing(self.root)["files"]]
        self.assertEqual(names, ["inside"])

    def test_listing_never_leaves_root(self):
        os.symlink(self.outside, os.path.join(self.root, "out"))
        os.symlink("/", os.path.join(self.root, "slash"))
        _plant(self.root, "a/b/c.txt")
        listed = files.listing(self.root)
        names = [r["name"] for r in listed["files"]]
        self.assertEqual(names, ["a", "a/b", "a/b/c.txt"])
        for name in names:
            self.assertFalse(name.startswith("/"), name)
            self.assertFalse(name.startswith(".."), name)
        self.assertNotIn("secret", repr(listed))
        self.assertNotIn("etc", names)

    # -- overwriting ---------------------------------------------------------

    def test_overwrite_without_replace_is_refused_and_keeps_the_original(self):
        files.write_one(self.root, "a.txt", b"first", max_bytes=BUDGET)
        with self.assertRaises(files.FilesError) as caught:
            files.write_one(self.root, "a.txt", b"second", max_bytes=BUDGET)
        self.assertIn("already exists", str(caught.exception))
        self.assertEqual(files.read_one(self.root, "a.txt", max_bytes=BUDGET),
                         b"first")
        files.write_one(self.root, "a.txt", b"second", max_bytes=BUDGET,
                        replace=True)
        self.assertEqual(files.read_one(self.root, "a.txt", max_bytes=BUDGET),
                         b"second")

    def test_a_directory_is_neither_read_nor_replaced_nor_deleted(self):
        os.makedirs(os.path.join(self.root, "dir"))
        _plant(self.root, "dir/kept.txt")
        with self.assertRaises(files.FilesError) as caught:
            files.read_one(self.root, "dir", max_bytes=BUDGET)
        self.assertIn("directory", str(caught.exception))
        with self.assertRaises(files.FilesError):
            files.write_one(self.root, "dir", b"x", max_bytes=BUDGET, replace=True)
        with self.assertRaises(files.FilesError):
            files.delete_one(self.root, "dir")
        self.assertTrue(os.path.exists(os.path.join(self.root, "dir", "kept.txt")))

    def test_a_regular_file_where_a_directory_is_named_is_refused(self):
        _plant(self.root, "notadir", b"x")
        with self.assertRaises(files.FilesError) as caught:
            files.write_one(self.root, "notadir/x.txt", b"x", max_bytes=BUDGET)
        self.assertIn("not a directory", str(caught.exception))

    # -- budgets -------------------------------------------------------------

    def test_a_file_over_budget_is_refused_BEFORE_it_is_read(self):
        """The size comes from fstat. If a single byte is read first, the
        budget is a cap on what is returned rather than on what is done."""
        _plant(self.root, "big.bin", b"x" * 100)
        with mock.patch.object(files.os, "read",
                               side_effect=AssertionError("read before size")):
            with self.assertRaises(files.FilesError) as caught:
                files.read_one(self.root, "big.bin", max_bytes=50)
        self.assertIn("budget", str(caught.exception))

    def test_a_file_that_grows_past_the_budget_mid_read_is_refused(self):
        """The size was read before the bytes. A runner appending in between
        must hit the cap on the read itself."""
        _plant(self.root, "grow.bin", b"x" * 10)
        real_fstat = files.os.fstat

        def lying_fstat(fd):
            info = real_fstat(fd)
            return os.stat_result((*info[:6], 1, *info[7:]))   # st_size = 1

        with mock.patch.object(files.os, "fstat", side_effect=lying_fstat):
            with self.assertRaises(files.FilesError) as caught:
                files.read_one(self.root, "grow.bin", max_bytes=5)
        self.assertIn("grew", str(caught.exception))

    def test_a_write_over_budget_is_refused_and_nothing_is_written(self):
        with self.assertRaises(files.FilesError):
            files.write_one(self.root, "big.bin", b"x" * (BUDGET + 1),
                            max_bytes=BUDGET)
        self.assertEqual(os.listdir(self.root), [])

    def test_listing_reports_incomplete_when_max_entries_is_hit(self):
        for index in range(20):
            _plant(self.root, f"f{index:02d}.txt")
        listed = files.listing(self.root, max_entries=5)
        self.assertFalse(listed["complete"])
        self.assertEqual(len(listed["files"]), 5)
        self.assertTrue(files.listing(self.root)["complete"])

    def test_listing_reports_incomplete_when_the_time_budget_is_hit(self):
        _plant(self.root, "a.txt")
        self.assertFalse(files.listing(self.root, budget_seconds=-1.0)["complete"])

    # -- atomicity -----------------------------------------------------------

    def test_an_atomic_write_leaves_no_temp_file_behind(self):
        files.write_one(self.root, "a.txt", b"x", max_bytes=BUDGET)
        files.write_one(self.root, "a.txt", b"y", max_bytes=BUDGET, replace=True)
        self.assertEqual(os.listdir(self.root), ["a.txt"])

    def test_a_failed_write_leaves_neither_a_temp_file_nor_a_partial_target(self):
        _plant(self.root, "a.txt", b"original")
        with mock.patch.object(files.os, "fsync",
                               side_effect=OSError(5, "Input/output error")):
            with self.assertRaises(files.FilesError) as caught:
                files.write_one(self.root, "a.txt", b"new", max_bytes=BUDGET,
                                replace=True)
        self.assertIn("could not be written", str(caught.exception))
        self.assertEqual(os.listdir(self.root), ["a.txt"])
        with open(os.path.join(self.root, "a.txt"), "rb") as handle:
            self.assertEqual(handle.read(), b"original")

    def test_a_written_file_is_0644_and_its_new_parents_are_writable_by_the_runner(self):
        files.write_one(self.root, "sub/deep/x.txt", b"x", max_bytes=BUDGET)
        mode = stat.S_IMODE(os.stat(os.path.join(self.root, "sub/deep/x.txt")).st_mode)
        self.assertEqual(mode, 0o644)
        for directory in ("sub", "sub/deep"):
            with self.subTest(directory=directory):
                mode = stat.S_IMODE(os.stat(os.path.join(self.root, directory)).st_mode)
                self.assertEqual(mode, 0o777)

    def test_parent_directories_are_created_inside_root_only(self):
        files.write_one(self.root, "a/./b/../c/x.txt", b"x", max_bytes=BUDGET)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "a/c/x.txt")))
        self.assertEqual(sorted(os.listdir(self.root)), ["a"])
        self._outside_untouched()

    def test_a_missing_root_lists_empty_and_refuses_to_be_created_by_a_write(self):
        """A capsule never mounted, or released. Manufacturing its directory
        here would make `status()` call it mounted and give the sweeper a
        directory to explain."""
        gone = os.path.join(self.root, "nope")
        self.assertEqual(files.listing(gone), {"files": [], "complete": True})
        with self.assertRaises(files.FilesError) as caught:
            files.write_one(gone, "a.txt", b"x", max_bytes=BUDGET)
        self.assertIn("mount it first", str(caught.exception))
        self.assertFalse(os.path.exists(gone))
        with self.assertRaises(files.FilesError):
            files.read_one(gone, "a.txt", max_bytes=BUDGET)


class ManagerFilesTests(SimpleTestCase):
    """The lifetime rule and the argv, over a fake Docker."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="anastasia-files-mgr-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        # Pin the image lock to a path this test owns — see test_service for
        # why a fake driver must never meet a real /etc/anastasia lock.
        lock = mock.patch.dict(
            os.environ,
            {"ANASTASIA_IMAGE_LOCK": os.path.join(self.root, "images.lock.json")})
        lock.start()
        self.addCleanup(lock.stop)
        self.docker = FakeDocker()
        self.manager = capsules.CapsuleManager(
            staging_root=self.root, slice_driver=CountingSliceDriver(),
            docker=self.docker, generation="gen-files")
        self.capsule = str(uuid.uuid4())
        self.limits = Limits(2000, 1024, 512, 256)

    def _start(self, capsule=None):
        execution = str(uuid.uuid4())
        self.manager.start_execution(
            capsule=capsule or self.capsule, execution=execution,
            operation="render_pdf", params={}, limits=Limits(1000, 512, 256, 64),
            timeout=60, payload=None)
        return execution

    def test_mount_creates_the_files_area_writable_by_nobody(self):
        self.manager.mount(self.capsule, self.limits)
        root = self.manager.files_root(self.capsule)
        self.assertTrue(os.path.isdir(root))
        self.assertEqual(stat.S_IMODE(os.stat(root).st_mode), 0o777)
        self.assertEqual(root, os.path.join(self.manager.capsule_dir(self.capsule),
                                            "files"))

    def test_unmount_keeps_the_files_area(self):
        """THE LIFETIME RULE. The area lives as long as the reservation, and
        an unmount is not the end of one."""
        self.manager.mount(self.capsule, self.limits)
        self._start()
        self.manager.file_put(self.capsule, "thesis.tex", b"\\documentclass")
        result = self.manager.unmount(self.capsule)

        self.assertFalse(result["purged"])
        self.assertEqual(result["runners_destroyed"], 1)
        self.assertFalse(os.path.isdir(self.manager.exec_root(self.capsule)),
                         "scratch is the mount's and goes with it")
        self.assertEqual(self.manager.file_get(self.capsule, "thesis.tex"),
                         b"\\documentclass")
        self.assertFalse(self.manager.status(self.capsule)["mounted"],
                         "a kept files area must not read as still mounted")

        # A remount finds it again.
        self.manager.mount(self.capsule, self.limits)
        names = [r["name"] for r in self.manager.files(self.capsule)["files"]]
        self.assertEqual(names, ["thesis.tex"])
        self.assertTrue(self.manager.status(self.capsule)["mounted"])

    def test_unmount_with_purge_removes_it(self):
        self.manager.mount(self.capsule, self.limits)
        self.manager.file_put(self.capsule, "thesis.tex", b"x")
        result = self.manager.unmount(self.capsule, purge=True)
        self.assertTrue(result["purged"])
        self.assertFalse(os.path.exists(self.manager.capsule_dir(self.capsule)))
        self.assertEqual(self.manager.files(self.capsule),
                         {"files": [], "complete": True})

    def test_unmount_is_idempotent_either_way(self):
        self.manager.mount(self.capsule, self.limits)
        for purge in (False, False, True, True):
            with self.subTest(purge=purge):
                self.manager.unmount(self.capsule, purge=purge)

    def test_a_job_carries_the_files_mount_right_after_out(self):
        self.manager.mount(self.capsule, self.limits)
        self._start()
        argv = self.docker.created_args[-1]
        mounts = [argv[i + 1] for i, flag in enumerate(argv) if flag == "--mount"]
        files_root = self.manager.files_root(self.capsule)
        self.assertEqual(mounts[-2].split(",")[-1], "target=/out")
        self.assertEqual(mounts[-1],
                         f"type=bind,source={files_root},target=/files")
        self.assertNotIn("readonly", mounts[-1])
        # And still before the image, where a flag has to stand.
        self.assertLess(argv.index(mounts[-1]), argv.index("anastasia-pdf"))

    def test_a_job_after_an_adoption_still_gets_a_writable_files_area(self):
        """No mount on THIS manager: a restarted executor adopting a running
        capsule. Docker would create a missing bind source as root 0755 and
        the runner could not write it."""
        self._start()
        root = self.manager.files_root(self.capsule)
        self.assertTrue(os.path.isdir(root))
        self.assertEqual(stat.S_IMODE(os.stat(root).st_mode), 0o777)

    def test_a_raw_runner_without_a_files_dir_gets_exactly_the_two_mounts_it_had(self):
        """The argv tests in test_egress and test_limits build without one;
        this pins that the flag is optional rather than defaulted."""
        from toto.anastasia.families import PDF
        argv = self.docker.create(
            family=PDF, limits=Limits(1000, 512, 256, 64), name="raw",
            cgroup_parent=None, input_dir="/tmp/in", output_dir="/tmp/out",
            env={}, labels={}, argv=["true"])
        args = self.docker.created_args[-1]
        self.assertEqual(args.count("--mount"), 2)
        self.assertNotIn("target=/files", " ".join(args))
        self.assertTrue(argv)

    def test_storage_reports_files_as_its_own_area_with_counts_only(self):
        self.manager.mount(self.capsule, self.limits)
        self.manager.file_put(self.capsule, "private-thesis.pdf", b"x" * 30)
        self.manager.file_put(self.capsule, "notes/private-notes.md", b"y" * 12)
        execution = self._start()
        with open(os.path.join(self.manager.exec_dir(self.capsule, execution),
                               "out", "output.pdf"), "wb") as handle:
            handle.write(b"z" * 7)

        reading = self.manager.storage(self.capsule)
        self.assertEqual(sorted(reading["areas"]), ["exec", "files"])
        self.assertEqual(reading["areas"]["files"]["bytes"], 42)
        self.assertEqual(reading["areas"]["files"]["files"], 2)
        self.assertEqual(reading["areas"]["exec"]["bytes"], 7)
        self.assertEqual(reading["bytes"], 49)
        _numbers_only(self, reading)
        for forbidden in ("private", "thesis", "notes", "output.pdf"):
            self.assertNotIn(forbidden, repr(reading))

    def test_the_manager_refuses_a_hostile_name_itself(self):
        """Not only the HTTP layer: a second caller must not reach the disk
        with a name nobody judged."""
        self.manager.mount(self.capsule, self.limits)
        for name in ("../x", "/etc/passwd"):
            with self.subTest(name=name):
                with self.assertRaises(files.FilesError):
                    self.manager.file_get(self.capsule, name)
                with self.assertRaises(files.FilesError):
                    self.manager.file_put(self.capsule, name, b"x")
                with self.assertRaises(files.FilesError):
                    self.manager.file_delete(self.capsule, name)

    def test_the_sweeper_keeps_a_known_capsules_files_and_removes_an_unknown_one(self):
        """`sweep_staging` is the one executor-side path that removes a files
        area — for a capsule the app no longer lists, which is a released
        lease. A listed capsule's files are not its business."""
        other = str(uuid.uuid4())
        for capsule in (self.capsule, other):
            self.manager.mount(capsule, self.limits)
            self.manager.file_put(capsule, "kept.txt", b"x")
        self.manager.unmount(self.capsule)       # unmounted, still reserved

        swept = reconcile.sweep_staging(self.manager, known_capsules=[self.capsule])

        self.assertEqual(swept["capsules"], 1)
        self.assertEqual(self.manager.file_get(self.capsule, "kept.txt"), b"x")
        self.assertFalse(os.path.exists(self.manager.capsule_dir(other)))


class FilesRouteTests(ServiceTestCase):
    """The four routes over a real socket."""

    def mount(self):
        return self.call("POST", f"/capsules/{self.capsule}/mount",
                         {"limits": Limits(2000, 1024, 512, 256).as_dict()})

    def put(self, name, data, **extra):
        return self.call("POST", f"/capsules/{self.capsule}/files/put",
                         {"name": name,
                          "data_b64": base64.b64encode(data).decode("ascii"),
                          **extra})

    def test_the_four_routes_resolve(self):
        self.mount()
        status, body = self.put("docs/a.txt", b"hello")
        self.assertEqual((status, body), (200, {"name": "docs/a.txt", "size": 5}))

        status, body = self.call("GET", f"/capsules/{self.capsule}/files")
        self.assertEqual(status, 200)
        self.assertTrue(body["complete"])
        self.assertEqual([r["name"] for r in body["files"]], ["docs", "docs/a.txt"])

        status, body = self.call("POST", f"/capsules/{self.capsule}/files/get",
                                 {"name": "docs/a.txt"})
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["data_b64"]), b"hello")
        self.assertEqual(body["bytes"], 5)

        status, body = self.call("POST", f"/capsules/{self.capsule}/files/delete",
                                 {"name": "docs/a.txt"})
        self.assertEqual((status, body), (200, {"name": "docs/a.txt",
                                                "deleted": True}))
        _, body = self.call("GET", f"/capsules/{self.capsule}/files")
        self.assertEqual([r["name"] for r in body["files"]], ["docs"])

    def test_the_name_travels_in_the_body_and_a_query_string_is_ignored(self):
        """The server strips `?...` before signing and routing, so a name in
        the query would be unauthenticated. Signed as the bare path, sent
        with a query naming a different file: the body wins."""
        self.mount()
        self.put("mine.txt", b"mine")
        self.put("other.txt", b"other")
        path = f"/capsules/{self.capsule}/files/get"
        body = protocol.encode({"name": "mine.txt"})
        sent = protocol.sign(secret=SECRET, method="POST", path=path, body=body)
        sent["Content-Type"] = "application/json"
        conn = executor_backend.UnixHTTPConnection(self.socket_path, timeout=10)
        try:
            conn.request("POST", path + "?name=other.txt", body=body, headers=sent)
            response = conn.getresponse()
            answer = json.loads(response.read())
        finally:
            conn.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(base64.b64decode(answer["data_b64"]), b"mine")

        # And a query string is not a way to supply the name at all.
        body = protocol.encode({})
        sent = protocol.sign(secret=SECRET, method="POST", path=path, body=body)
        sent["Content-Type"] = "application/json"
        conn = executor_backend.UnixHTTPConnection(self.socket_path, timeout=10)
        try:
            conn.request("POST", path + "?name=mine.txt", body=body, headers=sent)
            response = conn.getresponse()
            answer = json.loads(response.read())
        finally:
            conn.close()
        self.assertEqual(response.status, 400)
        self.assertIn("name", answer["error"])

    def test_a_refused_name_is_a_400_not_a_500(self):
        self.mount()
        cases = [
            ("get", {"name": "../x"}, "escapes"),
            ("put", {"name": "/etc/passwd", "data_b64": "eA=="}, "absolute"),
            ("delete", {"name": "a/../../x"}, "escapes"),
            ("get", {"name": "a\x00b"}, "NUL"),
            ("get", {"name": 5}, "name"),
            ("get", {}, "name"),
            ("put", {"name": "a.txt", "data_b64": "not base64!"}, "base64"),
        ]
        for verb, payload, phrase in cases:
            with self.subTest(verb=verb, payload=payload):
                status, body = self.call(
                    "POST", f"/capsules/{self.capsule}/files/{verb}", payload)
                self.assertEqual(status, 400, body)
                self.assertIn(phrase, body["error"])

    def test_a_missing_file_is_a_400_with_a_sentence(self):
        self.mount()
        status, body = self.call("POST", f"/capsules/{self.capsule}/files/get",
                                 {"name": "nope.txt"})
        self.assertEqual(status, 400)
        self.assertIn("no file named", body["error"])

    def test_a_symlink_a_runner_planted_is_a_400_through_the_wire_too(self):
        self.mount()
        os.symlink("/etc/hostname",
                   os.path.join(self.manager.files_root(self.capsule), "link"))
        status, body = self.call("POST", f"/capsules/{self.capsule}/files/get",
                                 {"name": "link"})
        self.assertEqual(status, 400)
        self.assertIn("symbolic link", body["error"])
        _, body = self.call("GET", f"/capsules/{self.capsule}/files")
        self.assertEqual(body["files"], [])

    def test_unmount_reads_purge_from_the_body(self):
        self.mount()
        self.put("kept.txt", b"x")
        status, body = self.call("POST", f"/capsules/{self.capsule}/unmount")
        self.assertEqual(status, 200)
        self.assertFalse(body["purged"])
        self.assertTrue(os.path.isfile(
            os.path.join(self.manager.files_root(self.capsule), "kept.txt")))

        status, body = self.call("POST", f"/capsules/{self.capsule}/unmount",
                                 {"purge": True})
        self.assertEqual(status, 200)
        self.assertTrue(body["purged"])
        self.assertFalse(os.path.exists(self.manager.capsule_dir(self.capsule)))

    def test_the_routes_are_in_the_table_the_docstring_counts(self):
        names = {name for _verb, _pattern, name in service.ROUTES}
        for name in ("capsule_files", "capsule_file_get", "capsule_file_put",
                     "capsule_file_delete"):
            self.assertIn(name, names)
        self.assertEqual(len(service.ROUTES), 22)
        self.assertIn("twenty-two routes", service.__doc__)


class BackendTests(ServiceTestCase):
    """The Django-side client against the real service: what a refusal and an
    outage each become."""

    def setUp(self):
        super().setUp()
        self.lease = SimpleNamespace(uuid=uuid.UUID(self.capsule),
                                     limits=Limits(2000, 1024, 512, 256),
                                     egress=False)
        self._settings = override_settings(
            ANASTASIA_EXECUTOR_SOCKET=self.socket_path,
            ANASTASIA_SHARED_SECRET=SECRET)
        self._settings.enable()
        self.addCleanup(self._settings.disable)
        self.backend = executor_backend.ExecutorRuntimeBackend()
        self.backend.mount(self.lease)

    def test_a_round_trip_through_the_client(self):
        out = self.backend.capsule_file_write(self.lease, "a.txt", b"hello")
        self.assertEqual(out, {"name": "a.txt", "size": 5})
        self.assertEqual(self.backend.capsule_file_read(self.lease, "a.txt"),
                         b"hello")
        listed = self.backend.capsule_files(self.lease)
        self.assertEqual([r["name"] for r in listed["files"]], ["a.txt"])
        self.assertEqual(self.backend.capsule_file_delete(self.lease, "a.txt"),
                         {"name": "a.txt", "deleted": True})

    def test_a_refusal_is_FilesRefused_carrying_the_executors_sentence(self):
        with self.assertRaises(executor_backend.FilesRefused) as caught:
            self.backend.capsule_file_read(self.lease, "../x")
        self.assertEqual(str(caught.exception),
                         "file escapes the files area: '../x'")
        self.assertNotIsInstance(caught.exception, RuntimeUnavailable)

        self.backend.capsule_file_write(self.lease, "a.txt", b"x")
        with self.assertRaises(executor_backend.FilesRefused):
            self.backend.capsule_file_write(self.lease, "a.txt", b"y")
        self.backend.capsule_file_write(self.lease, "a.txt", b"y", replace=True)
        self.assertEqual(self.backend.capsule_file_read(self.lease, "a.txt"), b"y")

    def test_a_missing_file_is_a_refusal_not_an_outage(self):
        with self.assertRaises(executor_backend.FilesRefused):
            self.backend.capsule_file_read(self.lease, "nope.txt")

    def test_a_transport_failure_is_still_RuntimeUnavailable(self):
        with override_settings(ANASTASIA_EXECUTOR_SOCKET=os.path.join(
                self.root, "nobody-listens.sock")):
            with self.assertRaises(RuntimeUnavailable):
                self.backend.capsule_file_read(self.lease, "a.txt")
            with self.assertRaises(RuntimeUnavailable):
                self.backend.capsule_file_write(self.lease, "a.txt", b"x")
            # The listing degrades like `storage`: an empty DICT, which is
            # not an empty listing.
            self.assertEqual(self.backend.capsule_files(self.lease), {})

    def test_unmount_sends_purge_and_defaults_to_keeping_the_files(self):
        self.backend.capsule_file_write(self.lease, "kept.txt", b"x")
        answer = self.backend.unmount(self.lease)
        self.assertFalse(answer["purged"])
        self.assertTrue(os.path.isfile(
            os.path.join(self.manager.files_root(self.capsule), "kept.txt")))
        answer = self.backend.unmount(self.lease, purge=True)
        self.assertTrue(answer["purged"])
        self.assertFalse(os.path.exists(self.manager.capsule_dir(self.capsule)))


class DjangoFreeTests(SimpleTestCase):
    def test_files_imports_without_django(self):
        """Listed in test_django_free's DJANGO_FREE too; this one names the
        module in its own failure."""
        import subprocess
        import sys
        from pathlib import Path

        # The VENDORED tree, put first on the path by hand. The venv also
        # holds an installed toto-anastasia, and a probe that does not say
        # which one it means imports whichever `sys.path` reaches first —
        # which is the wheel, which predates this module.
        src = Path(__file__).resolve().parents[3]
        probe = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, {str(src)!r}); "
             "import toto.anastasia.executor.files as m; "
             "assert 'django' not in sys.modules; "
             f"assert m.__file__.startswith({str(src)!r}), m.__file__; "
             "print('clean')"],
            capture_output=True, text=True)
        self.assertIn("clean", probe.stdout, probe.stderr)
