"""Submitting work: capsule admission, the narrow API, and honest closure."""

from __future__ import annotations

from unittest import mock

from django.core.exceptions import ValidationError
from django.test import override_settings

from toto.anastasia import choices, execute, families, services
from toto.anastasia.limits import Limits
from toto.anastasia.models import Execution

from .base import RUNNABLE, SMALL, AnastasiaTestCase, FakeRuntimeBackend


class SubmitTests(AnastasiaTestCase):
    def setUp(self):
        super().setUp()
        # Deliberately NOT the whole pool: these tests also reserve a second,
        # tiny Capsule to prove the "too big for this Capsule" refusal.
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=Limits(3000, 6144, 6144, 768))
        services.mount(lease=self.lease)

    def test_a_submitted_job_runs_and_books_against_the_capsule(self):
        before = services.capsule_available(self.lease)
        job = execute.submit(lease=self.lease, operation="render_pdf",
                             requested_by=self.user)
        self.assertEqual(job.status, choices.RUNNING)
        self.assertEqual(job.family, "pdf")
        after = services.capsule_available(self.lease)
        self.assertEqual(after.ram_mb,
                         before.ram_mb - families.PDF.default_limits.ram_mb)

    def test_the_pool_is_not_charged_twice(self):
        """An execution books against its GEAR, never against the pool again —
        the pool already gave that capacity away at reservation time."""
        before = services.available()
        execute.submit(lease=self.lease, operation="render_pdf")
        self.assertEqual(services.available().as_dict(), before.as_dict())

    def test_several_jobs_share_one_capsule_until_it_is_full(self):
        """A Capsule is a little pool: BUSY does not mean exclusive."""
        # Explicit limits so the arithmetic is visible here rather than
        # depending on what the family defaults happen to be: the Capsule holds
        # 3000 mCPU, so exactly three of these fit.
        third = {"cpu_millicores": 1000, "ram_mb": 1024,
                 "scratch_mb": 1024, "pids": 128}
        for _ in range(3):
            execute.submit(lease=self.lease, operation="render_pdf",
                           limits=third)
        self.assertEqual(self.lease.executions.live().count(), 3)
        with self.assertRaises(ValidationError) as caught:
            execute.submit(lease=self.lease, operation="render_pdf",
                           limits=third)
        self.assertEqual(caught.exception.refusal_code, services.CAPSULE_FULL)

    def test_finishing_a_job_returns_its_share_of_the_capsule(self):
        job = execute.submit(lease=self.lease, operation="render_pdf")
        during = services.capsule_available(self.lease)
        execute.finish(job, exit_code=0, usage={"cpu_seconds": 3})
        after = services.capsule_available(self.lease)
        self.assertGreater(after.ram_mb, during.ram_mb)
        self.assertEqual(after.as_dict(), self.lease.limits.as_dict())

    def test_a_job_bigger_than_the_capsule_says_so_rather_than_blaming_traffic(self):
        small = services.reserve(owner=self.other, name="tiny", limits=SMALL)
        services.mount(lease=small)
        with self.assertRaises(ValidationError) as caught:
            execute.submit(lease=small, operation="render_pdf")
        self.assertEqual(caught.exception.refusal_code,
                         services.TOO_BIG_FOR_CAPSULE)
        self.assertIn("in total", str(caught.exception))

    def test_a_caller_may_ask_for_less_than_the_family_default(self):
        small = services.reserve(owner=self.other, name="tiny", limits=SMALL)
        services.mount(lease=small)
        job = execute.submit(lease=small, operation="render_pdf",
                             limits={"cpu_millicores": 300, "ram_mb": 256,
                                     "scratch_mb": 128, "pids": 64})
        self.assertEqual(job.cpu_millicores, 300)

    def test_a_zero_dimension_means_use_the_family_default_for_it(self):
        job = execute.submit(lease=self.lease, operation="render_pdf",
                             limits={"ram_mb": 700})
        self.assertEqual(job.ram_mb, 700)
        self.assertEqual(job.cpu_millicores,
                         families.PDF.default_limits.cpu_millicores)


class NotAcceptingTests(AnastasiaTestCase):
    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=RUNNABLE)

    def test_an_unmounted_capsule_refuses_and_names_the_page(self):
        """Conscious provisioning: there is no auto-mount fallback."""
        with self.assertRaises(ValidationError) as caught:
            execute.submit(lease=self.lease, operation="render_pdf")
        self.assertEqual(caught.exception.refusal_code, services.NOT_MOUNTED)
        self.assertIn("not mounted", str(caught.exception))

    def test_a_released_capsule_refuses(self):
        services.release(lease=self.lease)
        with self.assertRaises(ValidationError) as caught:
            execute.submit(lease=self.lease, operation="render_pdf")
        self.assertEqual(caught.exception.refusal_code, services.LEASE_CLOSED)

    def test_a_degraded_capsule_refuses_rather_than_feeding_it_more(self):
        services.mount(lease=self.lease)
        runtime = services.runtime_for(self.lease)
        runtime.last_sample = {"oom_kills": 2}
        from django.utils import timezone
        runtime.sampled_at = timezone.now()
        runtime.save()
        with self.assertRaises(ValidationError) as caught:
            execute.submit(lease=self.lease, operation="render_pdf")
        self.assertIn("degraded", str(caught.exception))


class NarrowApiTests(AnastasiaTestCase):
    """A caller must not be able to reach Docker through the operation API.

    These are the tests that make the security model structural rather than
    aspirational: the vocabulary has no word for an image, a mount, a flag or a
    command, so there is nothing to filter.
    """

    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=RUNNABLE)
        services.mount(lease=self.lease)

    def test_docker_shaped_parameters_are_refused_by_name(self):
        for hostile in (
            {"image": "alpine"},
            {"command": "/bin/sh"},
            {"volumes": ["/:/host"]},
            {"privileged": True},
            {"cap_add": ["SYS_ADMIN"]},
            {"network": "host"},
            {"user": "0:0"},
            {"entrypoint": "sh"},
            {"security_opt": ["seccomp=unconfined"]},
            {"pid": "host"},
        ):
            with self.subTest(param=next(iter(hostile))):
                with self.assertRaises(ValidationError) as caught:
                    execute.submit(lease=self.lease, operation="render_pdf",
                                   params=hostile)
                self.assertIn("does not take", str(caught.exception))
                self.assertFalse(Execution.objects.exists())

    def test_an_unknown_operation_is_refused_and_lists_the_real_ones(self):
        with self.assertRaises(families.ParamError) as caught:
            execute.submit(lease=self.lease, operation="docker_run")
        self.assertIn("compile_latex", str(caught.exception))

    def test_a_path_parameter_cannot_escape_the_staged_input(self):
        for hostile in ("../../etc/passwd", "/etc/passwd", "a/../../b"):
            with self.subTest(path=hostile):
                with self.assertRaises(ValidationError):
                    execute.submit(lease=self.lease, operation="compile_latex",
                                   params={"main": hostile})

    def test_a_shell_metacharacter_cannot_reach_a_command_line(self):
        with self.assertRaises(ValidationError):
            execute.submit(lease=self.lease, operation="run_ocr",
                           params={"input": "s.png", "lang": "eng; id"})

    def test_a_media_command_is_named_never_typed(self):
        """The old fileservices path let a user type ffmpeg arguments and
        defended itself by rejecting shell tokens. A closed command set needs
        no such defence: there is no user text on the command line at all."""
        with self.assertRaises(ValidationError):
            execute.submit(lease=self.lease, operation="run_media_command",
                           params={"input": "v.mp4",
                                   "command": "-vf drawtext=x"})

    def test_the_timeout_is_bounded_by_the_operation(self):
        with self.assertRaises(ValidationError):
            execute.submit(lease=self.lease, operation="render_pdf",
                           timeout=999_999)

    def test_no_row_survives_a_refused_submission(self):
        with self.assertRaises(ValidationError):
            execute.submit(lease=self.lease, operation="render_pdf",
                           params={"image": "evil"})
        self.assertEqual(Execution.objects.count(), 0)


class ClosureTests(AnastasiaTestCase):
    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=RUNNABLE)
        services.mount(lease=self.lease)

    def test_a_backend_that_cannot_start_closes_the_row(self):
        """A PENDING row nobody will ever pick up is the bug every dispatcher
        in this codebase documents."""
        FakeRuntimeBackend.fail_start = True
        with self.assertRaises(ValidationError):
            execute.submit(lease=self.lease, operation="render_pdf")
        job = Execution.objects.get()
        self.assertTrue(job.is_finished)
        self.assertIn("no runner could be created", job.error)

    def test_finish_is_idempotent(self):
        job = execute.submit(lease=self.lease, operation="render_pdf")
        execute.finish(job, exit_code=0)
        first = job.finished_at
        execute.finish(job, exit_code=1)
        job.refresh_from_db()
        self.assertEqual(job.status, choices.SUCCESS)
        self.assertEqual(job.finished_at, first)

    def test_a_nonzero_exit_fails_the_row_with_a_sentence(self):
        job = execute.submit(lease=self.lease, operation="render_pdf")
        execute.finish(job, exit_code=137)
        self.assertEqual(job.status, choices.FAILED)
        self.assertIn("137", job.error)

    def test_unmounting_kills_running_jobs_rather_than_leaving_them_running(self):
        job = execute.submit(lease=self.lease, operation="render_pdf")
        services.unmount(lease=self.lease)
        job.refresh_from_db()
        self.assertEqual(job.status, choices.KILLED)
        self.assertIsNotNone(job.finished_at)

    def test_the_sweeper_closer_marks_a_stranded_job_lost(self):
        job = execute.submit(lease=self.lease, operation="render_pdf")
        execute.close_stuck(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, choices.LOST)

    def test_the_sweeper_closer_tolerates_a_vanished_row(self):
        execute.close_stuck(999999)      # must not raise

    def test_killing_is_idempotent_and_reaches_the_backend(self):
        job = execute.submit(lease=self.lease, operation="render_pdf")
        execute.kill(job, reason="user pressed stop")
        self.assertIn(("kill", str(job.uuid)), FakeRuntimeBackend.calls)
        execute.kill(job)
        job.refresh_from_db()
        self.assertEqual(job.status, choices.KILLED)
        self.assertIn("stop", job.error)


class MeteringRefusalTests(AnastasiaTestCase):
    """The two ways toto.quota can say no, and neither may be a traceback.

    ``check_quota`` raises TWO distinct exceptions, and the second one is easy
    to miss: ``InArrears`` is raised BEFORE it looks for a policy, so it fires
    on a host where anastasia has no policy row at all. Catching only
    ``QuotaExceeded`` let it escape ``submit()`` uncaught — a 500 on the one
    code path whose entire job is to refuse in a sentence a person can act on.

    They stay separate refusal codes for the reason toto.quota keeps the
    exceptions separate: "over a rate limit, try later" and "a levy went
    unpaid, top up" have different fixes, and one message for both tells half
    the users the wrong thing.
    """

    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=Limits(3000, 6144, 6144, 768))
        services.mount(lease=self.lease)

    def _submit(self):
        return execute.submit(lease=self.lease, operation="render_pdf",
                              requested_by=self.user)

    def test_a_spent_allowance_refuses_with_its_own_code(self):
        from toto.quota import QuotaExceeded

        from toto.anastasia.models import AnastasiaQuotaPolicy

        # A real policy row, not a stub: QuotaExceeded.__str__ reads
        # policy.period/name/unit, and the sentence it builds is what the user
        # is shown, so a stub would test a message nobody ever sees.
        policy = AnastasiaQuotaPolicy.objects.create(
            metric_code="anastasia.execution", limit=200)

        def boom(*a, **k):
            raise QuotaExceeded(policy, 200, 200)

        with mock.patch("toto.quota.check_quota", boom):
            with self.assertRaises(execute.CannotExecute) as caught:
                self._submit()
        self.assertEqual(caught.exception.refusal_code, services.QUOTA_EXCEEDED)
        self.assertFalse(Execution.objects.exists(),
                         "a refused submission must leave no row behind")

    def test_arrears_refuses_rather_than_escaping_as_a_500(self):
        from toto.quota import InArrears

        def boom(*a, **k):
            raise InArrears()

        with mock.patch("toto.quota.check_quota", boom):
            with self.assertRaises(execute.CannotExecute) as caught:
                self._submit()
        self.assertEqual(caught.exception.refusal_code, services.IN_ARREARS)
        self.assertNotEqual(caught.exception.refusal_code,
                            services.QUOTA_EXCEEDED,
                            "arrears is a payment problem, not a rate one")
        self.assertFalse(Execution.objects.exists())

    def test_both_exceptions_are_reachable_from_the_package_root(self):
        """A caller doing the documented thing must be able to name them.

        ``InArrears`` was absent from ``toto.quota.__all__`` while
        ``check_quota`` was exported, which is how the miss above happened.
        """
        import toto.quota as quota

        for name in ("check_quota", "QuotaExceeded", "InArrears"):
            with self.subTest(name=name):
                self.assertIn(name, quota.__all__)
                self.assertTrue(hasattr(quota, name))


class UnobservableKillTests(AnastasiaTestCase):
    """What a user is told when the tier cannot see why their job died.

    `oom_killed` is authoritative only where the HOST does the killing. On a VM
    tier the guest kernel does it and nothing crosses back, so False there means
    "could not tell" rather than "it had memory to spare" — see
    `Driver.observes_guest_oom`. Measured 2026-09-10: the same job, same
    ceiling, reports 137/true under runc and 255/false under Kata.

    Saying "it ran out of memory" anyway would print a guess as a fact. Saying
    nothing sends the user to read their own code when the answer is a bigger
    Capsule. So the sentence names the uncertainty and still gives the advice.
    """

    def _execution(self):
        from toto.anastasia.models import Execution

        lease = services.reserve(owner=self.user, name="c", limits=SMALL)
        services.mount(lease=lease, actor=self.user)
        return Execution.objects.create(
            lease=lease, operation="render_pdf", family="pdf",
            cpu_millicores=500, ram_mb=128, scratch_mb=64, pids=32,
            timeout_seconds=60, requested_by=self.user)

    def _finish_with(self, **status):
        from toto.anastasia import jobs

        execution = self._execution()
        base = {"found": True, "running": False, "exit_code": 255, "logs": ""}
        base.update(status)
        jobs._finish(execution, base, {})
        execution.refresh_from_db()
        return execution

    def test_an_observable_oom_still_says_out_of_memory(self):
        """The Docker path is untouched: where the host CAN see the kill, the
        confident sentence is right and must not start hedging."""
        execution = self._finish_with(oom_killed=True, oom_observable=True,
                                      exit_code=137)
        self.assertIn("ran out of memory", execution.error)

    def test_an_unobservable_kill_admits_it_cannot_tell(self):
        execution = self._finish_with(oom_killed=False, oom_observable=False,
                                      exit_code=255)
        self.assertIn("cannot see whether it ran out of memory",
                      execution.error)
        self.assertIn("more RAM", execution.error,
                      "naming the uncertainty is not enough on its own — the "
                      "user still needs to be told what to try")
        self.assertNotIn("ran out of memory inside", execution.error,
                         "must not assert the OOM it could not observe")

    def test_a_missing_key_keeps_the_old_behaviour(self):
        """AN OLDER EXECUTOR DOES NOT SEND THE KEY.

        The branch guards on `is False`, not on falsiness, so a status dict
        from an executor that predates this reads as "no opinion" and takes the
        exit-code path exactly as before. Getting this wrong would make every
        ordinary failure on every tier start hedging about memory.
        """
        execution = self._finish_with(oom_killed=False, exit_code=2)
        self.assertNotIn("cannot see whether", execution.error)

    def test_a_clean_exit_is_never_reported_as_a_kill(self):
        """exit 0 with the flag off must not trip the new branch."""
        execution = self._finish_with(oom_killed=False, oom_observable=False,
                                      exit_code=0)
        self.assertNotIn("cannot see whether", execution.error or "")
