"""The install path: a job that is watched, not waited on (todo 9.4).

Unit-level, against the shared fake runtime. What is asserted is what the
code BUILDS (an argv, a row, a phase and a count) and what it REFUSES (a
Capsule without internet access, a version in a name, a stranger's run); what
pip actually does inside a Capsule is on the manual list in test.md.
"""

from __future__ import annotations

import json

from django.test import SimpleTestCase

from toto.anastasia import choices, execute, families, install, services, tasks
from toto.anastasia.executor import runners
from toto.anastasia.models import Execution, InstallRun
from toto.anastasia.tokens import CapsuleToken

from .base import RUNNABLE, SMALL, AnastasiaTestCase, FakeRuntimeBackend


def _egress_capsule(owner, name="lab", limits=RUNNABLE, mount=True):
    lease = services.reserve(owner=owner, name=name, limits=limits)
    lease.egress = True
    lease.save(update_fields=["egress"])
    if mount:
        services.mount(lease=lease, actor=owner)
    return lease


PIP_RESOLVING = "Collecting numpy\n  Downloading numpy-2.1.0-cp312.whl (16 MB)\n"
PIP_INSTALLING = ("Collecting pandas\n  Downloading pandas-2.2.3.whl (12 MB)\n"
                  "Installing collected packages: numpy, pandas\n")
PIP_DONE = "Successfully installed numpy-2.1.0 pandas-2.2.3\n"


class ArgvTests(SimpleTestCase):
    """What reaches the runner. Shape only; pip is not run here."""

    def test_pip_is_asked_for_each_name_separately_into_the_files_area(self):
        op = families.operation("install_packages")
        argv = runners.build_argv(op, op.clean({"dists": "numpy+pandas"}))
        self.assertEqual(argv[-2:], ["numpy", "pandas"])
        self.assertIn("--target", argv)
        self.assertEqual(argv[argv.index("--target") + 1], runners.SITE_PACKAGES)
        self.assertEqual(argv[argv.index("--target") + 1], "/files/site-packages")
        self.assertIn("--no-input", argv)
        self.assertNotIn("--no-index", argv)

    def test_pip_gets_a_writable_temp_and_home(self):
        """The rootfs is read-only; without these pip dies unpacking its first
        wheel, with an error that reads as a broken package."""
        op = families.operation("install_packages")
        argv = runners.build_argv(op, op.clean({"dists": "numpy"}))
        self.assertEqual(argv[0], "env")
        self.assertIn("TMPDIR=/scratch", argv[:5])
        self.assertIn("HOME=/scratch", argv[:5])

    def test_a_script_sees_what_was_installed(self):
        op = families.operation("run_python")
        argv = runners.build_argv(op, op.clean({}))
        self.assertIn(f"PYTHONPATH={runners.SITE_PACKAGES}", argv)
        self.assertIn("anastasia-run-python", argv)

    def test_every_operation_has_an_argv(self):
        """families.py and runners.py drift apart silently otherwise: the
        KeyError is raised at execution time, inside the executor."""
        for name in families.OPERATIONS:
            with self.subTest(operation=name):
                self.assertIn(name, runners._BUILDERS)


class StartTests(AnastasiaTestCase):
    def test_a_capsule_without_internet_access_is_refused_before_anything(self):
        lease = services.reserve(owner=self.user, name="dark", limits=RUNNABLE)
        services.mount(lease=lease, actor=self.user)
        with self.assertRaises(execute.CannotExecute) as caught:
            install.start(lease=lease, dists="numpy", requested_by=self.user)
        self.assertEqual(caught.exception.refusal_code, install.NO_EGRESS)
        self.assertIn("internet access", str(caught.exception))
        self.assertEqual(InstallRun.objects.count(), 0)
        self.assertEqual(Execution.objects.count(), 0)

    def test_a_version_in_a_name_is_a_sentence_not_a_500(self):
        lease = _egress_capsule(self.user)
        with self.assertRaises(execute.CannotExecute) as caught:
            install.start(lease=lease, dists="numpy==1.26", requested_by=self.user)
        self.assertIn("no versions", str(caught.exception))
        self.assertEqual(InstallRun.objects.count(), 0)

    def test_it_starts_a_python_job_and_opens_the_run(self):
        lease = _egress_capsule(self.user)
        run = install.start(lease=lease, dists="NumPy+pandas",
                            requested_by=self.user)
        self.assertEqual(run.status, choices.RUNNING)
        self.assertEqual(run.phase, "resolving")
        self.assertEqual(run.packages, ["numpy", "pandas"])
        self.assertEqual((run.packages_done, run.packages_total), (0, 2))
        self.assertEqual(run.execution.operation, "install_packages")
        self.assertEqual(run.execution.family, "python")
        # The execution carries the run as its subject, so the trail can be
        # walked from either end.
        self.assertEqual(run.execution.subject_id, str(run.uuid))
        starts = [c for c in FakeRuntimeBackend.calls if c[0] == "start"]
        self.assertEqual(starts[0][2], {"dists": "numpy+pandas"})

    def test_a_refused_submission_leaves_no_run_behind(self):
        """The Capsule is reserved but not mounted, so submit refuses. The
        audit line submit writes is the trail; an orphan run would be a
        second, contradictory one."""
        lease = _egress_capsule(self.user, mount=False)
        with self.assertRaises(execute.CannotExecute):
            install.start(lease=lease, dists="numpy", requested_by=self.user)
        self.assertEqual(InstallRun.objects.count(), 0)


class RefreshTests(AnastasiaTestCase):
    def setUp(self):
        super().setUp()
        self.lease = _egress_capsule(self.user)
        self.run = install.start(lease=self.lease, dists="numpy+pandas",
                                 requested_by=self.user)

    def test_the_log_is_copied_and_the_phase_and_count_follow_it(self):
        FakeRuntimeBackend.log_text = PIP_RESOLVING
        run = install.refresh(self.run)
        self.assertEqual(run.log, PIP_RESOLVING)
        self.assertEqual(run.phase, "downloading")
        self.assertEqual(run.packages_done, 0)
        self.assertEqual(run.status, choices.RUNNING)

        FakeRuntimeBackend.log_text = PIP_RESOLVING + PIP_INSTALLING
        run = install.refresh(run)
        self.assertEqual(run.phase, "installing")
        self.assertEqual(run.packages_done, 0)

        FakeRuntimeBackend.log_text = PIP_RESOLVING + PIP_INSTALLING + PIP_DONE
        run = install.refresh(run)
        self.assertEqual(run.packages_done, 2)
        self.assertEqual(run.status, choices.RUNNING, "not settled yet")

    def test_the_cursor_advances_so_nothing_is_copied_twice(self):
        FakeRuntimeBackend.log_text = PIP_RESOLVING
        install.refresh(self.run)
        run = install.refresh(self.run)
        self.assertEqual(run.log, PIP_RESOLVING)
        self.assertEqual(run.log_offset, len(PIP_RESOLVING.encode("utf-8")))

    def test_it_closes_when_the_runner_has_exited(self):
        FakeRuntimeBackend.log_text = PIP_RESOLVING + PIP_INSTALLING + PIP_DONE
        FakeRuntimeBackend.settled = {"found": True, "running": False,
                                      "exit_code": 0, "oom_killed": False}
        run = install.refresh(self.run)
        self.assertEqual(run.status, choices.SUCCESS)
        self.assertEqual(run.phase, "finished")
        self.assertEqual(run.packages_done, 2)
        self.assertIn("Installed 2 of 2", run.detail)
        self.assertIsNotNone(run.finished_at)
        run.execution.refresh_from_db()
        self.assertEqual(run.execution.status, choices.SUCCESS)
        # The runner is destroyed like any finished job's.
        self.assertIn(("finish", str(run.execution.uuid)),
                      FakeRuntimeBackend.calls)

    def test_a_green_exit_that_named_too_few_packages_says_so(self):
        """pip exited 0 and named one of two. Not a failure, not a clean
        success either — a sentence pointing at the log."""
        FakeRuntimeBackend.log_text = "Successfully installed numpy-2.1.0\n"
        FakeRuntimeBackend.settled = {"found": True, "running": False,
                                      "exit_code": 0}
        run = install.refresh(self.run)
        self.assertEqual(run.status, choices.SUCCESS)
        self.assertEqual(run.packages_done, 1)
        self.assertIn("only 1 of 2", run.detail)

    def test_a_failed_exit_closes_with_the_jobs_sentence(self):
        FakeRuntimeBackend.log_text = "ERROR: No matching distribution found\n"
        FakeRuntimeBackend.settled = {"found": True, "running": False,
                                      "exit_code": 1}
        run = install.refresh(self.run)
        self.assertEqual(run.status, choices.FAILED)
        self.assertEqual(run.phase, "failed")
        run.execution.refresh_from_db()
        self.assertEqual(run.detail, run.execution.error)
        self.assertTrue(run.detail)

    def test_a_runner_the_manager_lost_is_lost(self):
        FakeRuntimeBackend.settled = {"found": False}
        run = install.refresh(self.run)
        self.assertEqual(run.status, choices.LOST)
        self.assertIn("lost track", run.detail)

    def test_an_already_satisfied_requirement_counts(self):
        FakeRuntimeBackend.log_text = ("Requirement already satisfied: numpy in "
                                       "/files/site-packages\n")
        run = install.refresh(self.run)
        self.assertEqual(run.packages_done, 1)
        self.assertEqual(run.phase, "resolving")

    def test_the_copy_is_capped_and_says_so_and_the_run_still_closes(self):
        FakeRuntimeBackend.log_text = "x" * (install.MAX_LOG_CHARS + 100)
        run = install.refresh(self.run)
        self.assertEqual(len(run.log), install.MAX_LOG_CHARS)
        self.assertTrue(run.log_truncated)
        FakeRuntimeBackend.settled = {"found": True, "running": False,
                                      "exit_code": 0}
        run = install.refresh(run)
        self.assertEqual(run.status, choices.SUCCESS)

    def test_a_silent_runtime_leaves_the_row_as_it_was(self):
        FakeRuntimeBackend.log_text = PIP_RESOLVING
        FakeRuntimeBackend.fail_logs = True
        run = install.refresh(self.run)
        self.assertEqual(run.log, "")
        self.assertEqual(run.log_offset, 0)
        self.assertEqual(run.status, choices.RUNNING)

    def test_the_last_lines_are_kept_when_it_exits_between_two_reads(self):
        """THE RACE THIS ORDER EXISTS FOR, and it needs a backend that has it.

        The real sequence is: read the log at offset N, ask whether the runner
        has exited, destroy it. pip's LAST lines — "Successfully installed …",
        the ones `packages_done` is counted from — are written in the moment
        between the first two, so a reader that does not read again before
        destroying loses exactly them, and every install that finished between
        two polls closes claiming it installed nothing.

        The shared fake cannot show this: its log is whatever the test set, so
        one read gets everything and the ordering makes no difference. This
        backend writes the final line WHEN THE EXIT IS OBSERVED, which is
        where a real runner writes it. Planted and confirmed on 2026-09-11:
        with the second read removed, this fails and nothing else does.
        """
        class ExitsMidRead(FakeRuntimeBackend):
            def execution_status(self, execution):
                # The last lines land now — after the caller's last read.
                type(self).log_text = PIP_RESOLVING + PIP_INSTALLING + PIP_DONE
                return {"found": True, "running": False, "exit_code": 0}

        globals()["ExitsMidRead"] = ExitsMidRead
        FakeRuntimeBackend.log_text = PIP_RESOLVING + PIP_INSTALLING
        with self.settings(ANASTASIA_RUNTIME_BACKEND=
                           "toto.anastasia.tests.test_install.ExitsMidRead"):
            run = install.refresh(self.run)
        self.assertIn("Successfully installed", run.log)
        self.assertEqual(run.packages_done, 2)
        self.assertIn("Installed 2 of 2", run.detail)

    def test_a_finished_run_is_not_polled_again(self):
        FakeRuntimeBackend.settled = {"found": True, "running": False,
                                      "exit_code": 0}
        run = install.refresh(self.run)
        before = len(FakeRuntimeBackend.calls)
        install.refresh(run)
        self.assertEqual(len(FakeRuntimeBackend.calls), before)

    def test_cancel_stops_the_job_and_closes_the_run_as_killed(self):
        run = install.cancel(self.run, actor=self.user, reason="changed my mind")
        self.assertEqual(run.status, choices.KILLED)
        self.assertEqual(run.phase, "failed")
        self.assertEqual(run.detail, "changed my mind")
        self.assertIn(("kill", str(run.execution.uuid)), FakeRuntimeBackend.calls)

    def test_the_beat_closes_a_run_nobody_watched(self):
        FakeRuntimeBackend.settled = {"found": True, "running": False,
                                      "exit_code": 0}
        result = tasks.reconcile()
        self.assertEqual(result["installs_closed"], 1)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, choices.SUCCESS)
        self.assertEqual(tasks.reconcile()["installs_closed"], 0)


class ApiTests(AnastasiaTestCase):
    def setUp(self):
        super().setUp()
        self.token_row, self.raw = CapsuleToken.issue(owner=self.user,
                                                      label="laptop")
        self.lease = _egress_capsule(self.user)

    def call(self, method, path, *, body=None, raw=None):
        kwargs = {"HTTP_AUTHORIZATION": f"Bearer {raw or self.raw}"}
        if body is not None:
            kwargs["content_type"] = "application/json"
            return getattr(self.client, method)(path, json.dumps(body), **kwargs)
        return getattr(self.client, method)(path, **kwargs)

    def _start(self, packages="numpy+pandas"):
        return self.call("post", f"/api/v1/capsules/{self.lease.uuid}/installs",
                         body={"packages": packages})

    def test_post_starts_an_install(self):
        response = self._start()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["phase"], "resolving")
        self.assertEqual(body["packages"], ["numpy", "pandas"])
        self.assertEqual(body["packages_total"], 2)
        self.assertFalse(body["finished"])

    def test_a_list_of_names_is_accepted_too(self):
        response = self._start(["numpy", "pandas"])
        self.assertEqual(response.status_code, 201, response.content)

    def test_no_packages_is_a_400_with_a_sentence(self):
        response = self._start("")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "bad_packages")

    def test_a_capsule_without_internet_is_a_409_naming_why(self):
        dark = services.reserve(owner=self.user, name="dark", limits=RUNNABLE)
        services.mount(lease=dark, actor=self.user)
        response = self.call("post", f"/api/v1/capsules/{dark.uuid}/installs",
                             body={"packages": "numpy"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], install.NO_EGRESS)

    def test_the_collection_is_not_shadowed_by_the_action_catch_all(self):
        """`capsules/<uuid>/<str:action>` swallowed /storage once and answered
        405. GET here must be the listing."""
        self._start()
        response = self.call("get",
                             f"/api/v1/capsules/{self.lease.uuid}/installs")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["installs"]), 1)

    def test_reading_a_run_advances_it_and_keeps_the_clients_place(self):
        uuid = self._start().json()["uuid"]
        FakeRuntimeBackend.log_text = PIP_RESOLVING
        first = self.call("get", f"/api/v1/installs/{uuid}").json()
        self.assertEqual(first["log"], PIP_RESOLVING)
        self.assertEqual(first["phase"], "downloading")
        FakeRuntimeBackend.log_text = PIP_RESOLVING + PIP_INSTALLING
        second = self.call(
            "get", f"/api/v1/installs/{uuid}?since={first['log_length']}").json()
        self.assertEqual(second["log"], PIP_INSTALLING)
        self.assertEqual(second["log_since"], len(PIP_RESOLVING))
        self.assertEqual(second["phase"], "installing")

    def test_a_junk_since_is_the_whole_log_not_a_500(self):
        uuid = self._start().json()["uuid"]
        response = self.call("get", f"/api/v1/installs/{uuid}?since=lots")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["log_since"], 0)

    def test_a_strangers_install_is_a_404(self):
        uuid = self._start().json()["uuid"]
        _row, other_raw = CapsuleToken.issue(owner=self.other, label="theirs")
        for method, path in (("get", f"/api/v1/installs/{uuid}"),
                             ("post", f"/api/v1/installs/{uuid}/cancel"),
                             ("get", f"/api/v1/capsules/{self.lease.uuid}/installs")):
            with self.subTest(path=path):
                response = self.call(method, path, raw=other_raw,
                                     body={} if method == "post" else None)
                self.assertEqual(response.status_code, 404)

    def test_cancel_kills_it(self):
        uuid = self._start().json()["uuid"]
        response = self.call("post", f"/api/v1/installs/{uuid}/cancel",
                             body={"reason": "wrong package"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], choices.KILLED)
        self.assertTrue(response.json()["finished"])

    def test_the_describe_shape_is_the_contract(self):
        body = self._start().json()
        for key in ("uuid", "capsule", "execution", "status", "finished",
                    "phase", "packages", "packages_done", "packages_total",
                    "detail", "log_length", "log_truncated", "created_at",
                    "started_at", "finished_at"):
            self.assertIn(key, body)
