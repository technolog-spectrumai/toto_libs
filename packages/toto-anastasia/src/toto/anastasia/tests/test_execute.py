"""Submitting work: gear admission, the narrow API, and honest closure."""

from __future__ import annotations

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
        # tiny Gear to prove the "too big for this Gear" refusal.
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=Limits(3000, 6144, 6144, 768))
        services.mount(lease=self.lease)

    def test_a_submitted_job_runs_and_books_against_the_gear(self):
        before = services.gear_available(self.lease)
        job = execute.submit(lease=self.lease, operation="render_pdf",
                             requested_by=self.user)
        self.assertEqual(job.status, choices.RUNNING)
        self.assertEqual(job.family, "pdf")
        after = services.gear_available(self.lease)
        self.assertEqual(after.ram_mb,
                         before.ram_mb - families.PDF.default_limits.ram_mb)

    def test_the_pool_is_not_charged_twice(self):
        """An execution books against its GEAR, never against the pool again —
        the pool already gave that capacity away at reservation time."""
        before = services.available()
        execute.submit(lease=self.lease, operation="render_pdf")
        self.assertEqual(services.available().as_dict(), before.as_dict())

    def test_several_jobs_share_one_gear_until_it_is_full(self):
        """A Gear is a little pool: BUSY does not mean exclusive."""
        # Explicit limits so the arithmetic is visible here rather than
        # depending on what the family defaults happen to be: the Gear holds
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
        self.assertEqual(caught.exception.refusal_code, services.GEAR_FULL)

    def test_finishing_a_job_returns_its_share_of_the_gear(self):
        job = execute.submit(lease=self.lease, operation="render_pdf")
        during = services.gear_available(self.lease)
        execute.finish(job, exit_code=0, usage={"cpu_seconds": 3})
        after = services.gear_available(self.lease)
        self.assertGreater(after.ram_mb, during.ram_mb)
        self.assertEqual(after.as_dict(), self.lease.limits.as_dict())

    def test_a_job_bigger_than_the_gear_says_so_rather_than_blaming_traffic(self):
        small = services.reserve(owner=self.other, name="tiny", limits=SMALL)
        services.mount(lease=small)
        with self.assertRaises(ValidationError) as caught:
            execute.submit(lease=small, operation="render_pdf")
        self.assertEqual(caught.exception.refusal_code,
                         services.TOO_BIG_FOR_GEAR)
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

    def test_an_unmounted_gear_refuses_and_names_the_page(self):
        """Conscious provisioning: there is no auto-mount fallback."""
        with self.assertRaises(ValidationError) as caught:
            execute.submit(lease=self.lease, operation="render_pdf")
        self.assertEqual(caught.exception.refusal_code, services.NOT_MOUNTED)
        self.assertIn("not mounted", str(caught.exception))

    def test_a_released_gear_refuses(self):
        services.release(lease=self.lease)
        with self.assertRaises(ValidationError) as caught:
            execute.submit(lease=self.lease, operation="render_pdf")
        self.assertEqual(caught.exception.refusal_code, services.LEASE_CLOSED)

    def test_a_degraded_gear_refuses_rather_than_feeding_it_more(self):
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

    def test_a_media_preset_is_named_never_typed(self):
        """The old fileservices path let a user type ffmpeg arguments and
        defended itself by rejecting shell tokens. A fixed preset needs no
        such defence: there is no user text on the command line at all."""
        with self.assertRaises(ValidationError):
            execute.submit(lease=self.lease, operation="normalize_media",
                           params={"input": "v.mp4",
                                   "preset": "-vf drawtext=x"})

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
