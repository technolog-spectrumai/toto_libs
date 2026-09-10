"""Driving the whole manager with a fake Docker: lifecycle and reconciliation."""

from __future__ import annotations

import io
import os
import shutil
import tarfile
import tempfile
from unittest import mock
import time
import uuid

from django.test import SimpleTestCase

from toto.anastasia.limits import Limits
from toto.anastasia.executor import capsules, reconcile, staging
from toto.anastasia.executor.drivers import docker as containers

from .fakes import CountingSliceDriver, FakeDocker


def tar_of(files: dict) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, body in files.items():
            data = body.encode() if isinstance(body, str) else body
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class ManagerTestCase(SimpleTestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="anastasia-mgr-test-")
        # PIN THE IMAGE LOCK TO A PATH THIS TEST OWNS.
        #
        # `images.load()` falls back to /etc/anastasia/images.lock.json, which
        # is a REAL FILE on any host with a deployment installed. A fake driver
        # reports fake digests, so they can never match a real lock, and four
        # tests here failed with a 409 the moment this machine had one — while
        # passing on any box that had never deployed. A suite whose result
        # depends on whether the host is a deployment target is not a suite.
        #
        # The path deliberately does not exist: an absent lock means UNPINNED,
        # which is the documented, allowed state and the one these tests want.
        _lock = mock.patch.dict(
            os.environ,
            {"ANASTASIA_IMAGE_LOCK": os.path.join(self.root, "images.lock.json")})
        _lock.start()
        self.addCleanup(_lock.stop)
        self.docker = FakeDocker()
        self.slices = CountingSliceDriver()
        self.manager = capsules.CapsuleManager(
            staging_root=self.root, slice_driver=self.slices,
            docker=self.docker, generation="gen-a")
        self.capsule = str(uuid.uuid4())
        self.limits = Limits(2000, 1024, 512, 256)
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def mount(self):
        return self.manager.mount(self.capsule, self.limits)

    def start(self, operation="render_pdf", params=None, payload=None,
              execution=None, timeout=60):
        return self.manager.start_execution(
            capsule=self.capsule, execution=execution or str(uuid.uuid4()),
            operation=operation, params=params or {},
            limits=Limits(1000, 512, 256, 64), timeout=timeout, payload=payload)


class MountTests(ManagerTestCase):
    def test_mounting_creates_the_ceiling_and_the_staging_area(self):
        result = self.mount()
        self.assertTrue(result["slice_enforced"])
        self.assertEqual(result["manager_generation"], "gen-a")
        self.assertIn(self.capsule, self.slices.ensured)
        self.assertTrue(os.path.isdir(self.manager.capsule_dir(self.capsule)))

    def test_a_host_that_cannot_make_cgroups_degrades_rather_than_failing(self):
        """Per-runner limits still apply and the booking still bounds the sum,
        so the Capsule works — it just has no hard backstop, and says so."""
        self.manager.slices = CountingSliceDriver(fail_ensure=True)
        result = self.mount()
        self.assertFalse(result["slice_enforced"])
        self.assertIn("bookkeeping only", result["detail"])
        self.assertTrue(os.path.isdir(self.manager.capsule_dir(self.capsule)))

    def test_unmounting_destroys_runners_ceiling_and_files(self):
        self.mount()
        self.start()
        self.start()
        result = self.manager.unmount(self.capsule)
        self.assertEqual(result["runners_destroyed"], 2)
        self.assertIn(self.capsule, self.slices.destroyed)
        self.assertFalse(os.path.exists(self.manager.capsule_dir(self.capsule)))
        self.assertEqual(self.docker.list_managed(capsule=self.capsule), [])

    def test_unmount_is_idempotent(self):
        self.mount()
        self.manager.unmount(self.capsule)
        again = self.manager.unmount(self.capsule)
        self.assertEqual(again["runners_destroyed"], 0)


class ExecutionTests(ManagerTestCase):
    def test_an_execution_stages_input_and_starts_a_runner(self):
        self.mount()
        execution = str(uuid.uuid4())
        self.start(operation="compile_latex", params={"main": "main.tex"},
                   payload=tar_of({"main.tex": "\\documentclass{article}"}),
                   execution=execution)
        staged = os.path.join(self.manager.exec_dir(self.capsule, execution),
                              "in", "main.tex")
        self.assertTrue(os.path.exists(staged))
        self.assertEqual(self.docker.list_managed(capsule=self.capsule)[0]["state"],
                         "running")

    def test_the_runner_argv_comes_from_the_catalogue_not_the_caller(self):
        self.mount()
        self.start(operation="compile_latex",
                   params={"main": "a.tex", "engine": "xelatex"})
        argv = list(self.docker.containers.values())[0]["argv"]
        self.assertEqual(argv[0], "anastasia-compile-latex")
        self.assertIn("--engine", argv)
        self.assertIn("xelatex", argv)

    def test_a_missing_runner_image_refuses_before_staging_anything(self):
        self.mount()
        self.docker.images.discard("anastasia-pdf")
        with self.assertRaises(capsules.CapsuleError):
            self.start(operation="render_pdf")
        self.assertEqual(self.docker.list_managed(capsule=self.capsule), [])

    def test_a_hostile_payload_leaves_nothing_behind(self):
        self.mount()
        execution = str(uuid.uuid4())
        evil = io.BytesIO()
        with tarfile.open(fileobj=evil, mode="w") as archive:
            info = tarfile.TarInfo("../escaped.txt")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(staging.StagingError):
            self.start(payload=evil.getvalue(), execution=execution)
        self.assertFalse(os.path.exists(
            self.manager.exec_dir(self.capsule, execution)))
        self.assertEqual(self.docker.list_managed(capsule=self.capsule), [])

    def test_a_retried_execution_id_does_not_inherit_the_last_attempt(self):
        self.mount()
        execution = str(uuid.uuid4())
        self.start(payload=tar_of({"first.txt": "1"}), execution=execution)
        self.manager.finish_execution(capsule=self.capsule, execution=execution)
        self.start(payload=tar_of({"second.txt": "2"}), execution=execution)
        staged = os.path.join(self.manager.exec_dir(self.capsule, execution), "in")
        self.assertEqual(sorted(os.listdir(staged)), ["second.txt"])

    def test_status_is_read_from_docker_not_remembered(self):
        self.mount()
        execution = str(uuid.uuid4())
        self.start(execution=execution)
        self.assertTrue(self.manager.execution_status(
            capsule=self.capsule, execution=execution)["running"])

        cid = list(self.docker.containers)[0]
        self.docker.finish(cid, exit_code=3, logs="it went wrong")
        status = self.manager.execution_status(capsule=self.capsule,
                                               execution=execution)
        self.assertFalse(status["running"])
        self.assertEqual(status["exit_code"], 3)
        self.assertIn("wrong", status["logs"])

    def test_an_oom_kill_is_reported_separately_from_the_exit_code(self):
        """Exit 137 is SIGKILL and says nothing about why — a deadline kill
        looks identical. Only OOMKilled distinguishes them."""
        self.mount()
        execution = str(uuid.uuid4())
        self.start(execution=execution)
        cid = list(self.docker.containers)[0]
        self.docker.finish(cid, exit_code=137, oom_killed=True)
        status = self.manager.execution_status(capsule=self.capsule,
                                               execution=execution)
        self.assertTrue(status["oom_killed"])

    def test_output_comes_back_as_a_tar(self):
        self.mount()
        execution = str(uuid.uuid4())
        self.start(execution=execution)
        out = os.path.join(self.manager.exec_dir(self.capsule, execution), "out")
        with open(os.path.join(out, "output.pdf"), "wb") as handle:
            handle.write(b"%PDF-1.4 fake")
        blob = self.manager.collect(capsule=self.capsule, execution=execution)
        names = tarfile.open(fileobj=io.BytesIO(blob)).getnames()
        self.assertEqual(names, ["output.pdf"])

    def test_finishing_removes_the_runner_and_its_scratch(self):
        self.mount()
        execution = str(uuid.uuid4())
        self.start(execution=execution)
        self.manager.finish_execution(capsule=self.capsule, execution=execution)
        self.assertEqual(self.docker.list_managed(capsule=self.capsule), [])
        self.assertFalse(os.path.exists(
            self.manager.exec_dir(self.capsule, execution)))
        # …twice, because reconciliation calls it speculatively.
        self.manager.finish_execution(capsule=self.capsule, execution=execution)


class ReconcileTests(ManagerTestCase):
    def test_a_runner_past_its_deadline_is_killed(self):
        """Deadlines live in a LABEL and are enforced by this loop, not by a
        thread — a thread dies with the manager and leaves the runner forever."""
        self.mount()
        self.start(timeout=1)
        killed = reconcile.enforce_deadlines(self.manager,
                                             now=time.time() + 3600)
        self.assertEqual(killed, 1)

    def test_a_runner_inside_its_deadline_is_left_alone(self):
        self.mount()
        self.start(timeout=600)
        self.assertEqual(reconcile.enforce_deadlines(self.manager), 0)

    # `test_a_warm_runner_has_no_deadline_to_outlive` stood here and is
    # deleted rather than adjusted, because the behaviour it asserted is
    # REVERSED: enforce_deadlines used to skip warm runners, and now checks
    # every running one. There are no warm runners to exempt (2026-09-10), and
    # a test asserting the exemption would be asserting a branch that can
    # never be taken.

    def test_a_runner_whose_capsule_is_gone_is_destroyed(self):
        self.mount()
        self.start()
        shutil.rmtree(self.manager.capsule_dir(self.capsule))
        self.assertEqual(reconcile.destroy_orphan_runners(self.manager), 1)
        self.assertEqual(self.docker.list_managed(), [])

    def test_the_callers_capsule_list_is_authoritative(self):
        """The manager does not know what a lease is and must not guess: a Capsule
        released while the manager was down is only knowable from the caller."""
        self.mount()
        self.start()
        self.assertEqual(
            reconcile.destroy_orphan_runners(self.manager, known_capsules=[]), 1)

    def test_a_known_capsule_survives_reconciliation(self):
        self.mount()
        self.start()
        self.assertEqual(
            reconcile.destroy_orphan_runners(self.manager,
                                             known_capsules=[self.capsule]), 0)

    def test_staging_for_a_live_execution_is_never_swept(self):
        self.mount()
        self.start()
        result = reconcile.sweep_staging(self.manager, known_capsules=[self.capsule],
                                         now=time.time() + 99999)
        self.assertEqual(result["executions"], 0)

    def test_abandoned_staging_is_swept_only_after_the_grace_window(self):
        """The window is what stops this racing an execution that has a
        directory but not yet a container."""
        self.mount()
        execution = str(uuid.uuid4())
        self.start(execution=execution)
        for cid in list(self.docker.containers):
            self.docker.remove(cid)

        fresh = reconcile.sweep_staging(self.manager, known_capsules=[self.capsule])
        self.assertEqual(fresh["executions"], 0)

        later = time.time() + reconcile.STAGING_GRACE_SECONDS + 60
        aged = reconcile.sweep_staging(self.manager, known_capsules=[self.capsule],
                                       now=later)
        self.assertEqual(aged["executions"], 1)

    def test_staging_for_an_unknown_capsule_goes_immediately(self):
        self.mount()
        self.start()
        result = reconcile.sweep_staging(self.manager, known_capsules=[])
        self.assertEqual(result["capsules"], 1)
        self.assertFalse(os.path.exists(self.manager.capsule_dir(self.capsule)))

    def test_a_container_labelled_with_no_capsule_is_destroyed(self):
        self.mount()
        self.start()
        for row in self.docker.containers.values():
            row["labels"][containers.LABEL_CAPSULE] = ""
        self.assertEqual(reconcile.destroy_orphan_runners(self.manager), 1)

    def test_a_restarted_manager_adopts_what_it_finds(self):
        """The whole reason the manager keeps no database: a new process
        rebuilds its view by looking, and cannot drift from reality."""
        self.mount()
        self.start()
        successor = capsules.CapsuleManager(
            staging_root=self.root, slice_driver=self.slices,
            docker=self.docker, generation="gen-b")
        inherited = reconcile.adopt(successor)
        self.assertEqual(inherited["runners"], 1)
        self.assertEqual(inherited["running"], 1)
        self.assertEqual(inherited["capsules"], [self.capsule])
        self.assertEqual(inherited["generation"], "gen-b")
        # And it can still drive the runner it never started.
        self.assertTrue(successor.execution_status(
            capsule=self.capsule,
            execution=self.docker.list_managed()[0]["execution"])["running"])

    def test_a_full_tick_is_safe_on_a_healthy_manager(self):
        self.mount()
        self.start(timeout=600)
        result = reconcile.tick(self.manager, known_capsules=[self.capsule])
        self.assertEqual(result["deadlines_enforced"], 0)
        self.assertEqual(result["orphans_destroyed"], 0)
        self.assertEqual(self.docker.list_managed(capsule=self.capsule)[0]["state"],
                         "running")
