"""What the compute tier writes to the append-only trail, and what it must not.

The trail is what an operator reads after an incident, so the questions it has
to answer are: who asked for this, which Capsule, what isolation did it actually
run under, and how did it end. Every test here is one of those questions.

The other half matters as much. A job's inputs are the user's documents, and a
trail that copied them would become a second place those documents live —
outside the vault, outside its permissions, and kept forever by design.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model

from toto.anastasia import execute, services
from toto.anastasia.limits import Limits

from .base import AnastasiaTestCase, FakeRuntimeBackend

RUNNABLE = Limits(cpu_millicores=2000, ram_mb=2048, scratch_mb=1024, pids=256)


def _records(action=None):
    """Rows this app wrote, oldest first.

    `toto.audit` UPPERCASES an action on the way in, so the stored value is
    ANASTASIA.JOB.START rather than the string the caller passed. Matched
    case-insensitively here so the test reads in the same case the code writes
    — and so a future change to that normalisation shows up as a failure
    rather than as a silently empty result set.
    """
    from toto.audit.models import AuditRecord

    rows = AuditRecord.objects.filter(app_label__iexact="anastasia")
    if action:
        rows = rows.filter(action__iexact=action)
    return list(rows.order_by("sequence"))


class JobAuditTests(AnastasiaTestCase):
    def setUp(self):
        super().setUp()
        FakeRuntimeBackend.reset()
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=RUNNABLE)
        services.mount(lease=self.lease, actor=self.user)

    def _run(self):
        return execute.submit(lease=self.lease, operation="render_pdf",
                              params={}, requested_by=self.user)

    def test_a_started_job_is_recorded_with_who_and_which_capsule(self):
        execution = self._run()
        rows = _records("anastasia.job.start")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.actor_user_id, self.user.pk)
        self.assertEqual(row.metadata["capsule"], str(self.lease.uuid))
        self.assertEqual(row.metadata["operation"], "render_pdf")
        self.assertEqual(row.object_id, str(execution.pk))

    def test_the_recorded_tier_is_the_one_the_capsule_was_mounted_under(self):
        """NOT what a setting asked for. The runtime row carries what the
        executor reported at mount time, and that is the only value an audit
        line may claim."""
        runtime = services.runtime_for(self.lease)
        runtime.tier = "kata"
        runtime.save(update_fields=["tier"])
        self.lease.refresh_from_db()

        self._run()
        self.assertEqual(_records("anastasia.job.start")[0].metadata["tier"],
                         "kata")

    def test_an_unknown_tier_is_recorded_as_absent_not_as_a_guess(self):
        self._run()
        # The fake backend reports no tier, so the runtime row is blank —
        # and a blank tier must be MISSING from the metadata rather than
        # defaulted to something that reads like a claim.
        self.assertNotIn("tier", _records("anastasia.job.start")[0].metadata)

    def test_a_finished_job_records_its_outcome(self):
        execution = self._run()
        execute.finish(execution, exit_code=0, usage={"cpu_seconds": 3})
        row = _records("anastasia.job.finish")[0]
        self.assertTrue(row.success)
        self.assertEqual(row.metadata["exit_code"], 0)
        self.assertEqual(row.metadata["usage"], {"cpu_seconds": 3})

    def test_a_failed_job_is_recorded_as_a_failure(self):
        execution = self._run()
        execute.finish(execution, exit_code=1)
        row = _records("anastasia.job.finish")[0]
        self.assertFalse(row.success)
        self.assertEqual(row.metadata["exit_code"], 1)

    def test_a_killed_job_says_somebody_stopped_it(self):
        execution = self._run()
        execute.kill(execution, reason="took too long")
        row = _records("anastasia.job.kill")[0]
        self.assertEqual(row.metadata["reason"], "took too long")

    def test_a_refused_job_leaves_a_trail(self):
        """The door most worth auditing: nothing ran, so nothing else records
        it, and 'why could they not run this' is a real support question."""
        services.release(lease=self.lease, actor=self.user)
        with self.assertRaises(execute.CannotExecute):
            self._run()
        rows = _records("anastasia.job.refuse")
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0].success)
        self.assertEqual(rows[0].metadata["capsule"], str(self.lease.uuid))
        self.assertTrue(rows[0].metadata["error"],
                        "the sentence the user saw must be recorded")

    def test_every_refusal_path_is_covered_by_the_wrapper(self):
        """There are ten `raise CannotExecute` sites and one audit call.

        Asserted on the CODE SHAPE rather than by exercising each path: the
        failure being guarded is an eleventh refusal added later that nobody
        instruments, and only the structure can rule that out.
        """
        import inspect

        source = inspect.getsource(execute.submit)
        self.assertIn("except CannotExecute", source)
        self.assertIn("anastasia.job.refuse", source)
        # The real work happens in _submit, so submit itself must not raise
        # anything the wrapper cannot see.
        self.assertIn("_submit(", source)


class AuditPrivacyTests(AnastasiaTestCase):
    """A trail that copied the user's data would be a second vault."""

    def setUp(self):
        super().setUp()
        FakeRuntimeBackend.reset()
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=RUNNABLE)
        services.mount(lease=self.lease, actor=self.user)

    def test_the_payload_is_never_recorded(self):
        secret = b"%PDF-1.4 the user's private document"
        execution = execute.submit(lease=self.lease, operation="render_pdf",
                                   params={}, payload=secret,
                                   requested_by=self.user)
        execute.finish(execution, exit_code=0)
        for row in _records():
            blob = str(row.metadata)
            with self.subTest(action=row.action):
                self.assertNotIn("private document", blob)
                self.assertNotIn("%PDF", blob)

    def test_no_metadata_key_would_be_redacted(self):
        """`toto.audit` replaces the value of any key containing "token" with
        [REDACTED]. A key that silently became that would make the trail lie
        about itself, so none of ours may contain it."""
        execution = execute.submit(lease=self.lease, operation="render_pdf",
                                   params={}, requested_by=self.user)
        execute.finish(execution, exit_code=0)
        for row in _records():
            for key in row.metadata:
                with self.subTest(action=row.action, key=key):
                    self.assertNotIn("token", key.lower())
