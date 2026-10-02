"""Every forum cleanup on the worker, as a run of the "Forum cleanup" workflow.

Stage 47.2–47.3 (2026-10-02). Four promises, each asserted directly:

* **No worker, nothing claimed.** The check comes before the claim, so a
  refused press leaves no RUNNING row behind to block the next one, and the
  page says why in a sentence.
* **One claim, one workflow run.** The room button, the forum-wide button and
  the nightly beat each claim their rows, stamp them with the WorkflowRun's id
  BEFORE queueing, and queue exactly one run — started by the staff member,
  or by nobody (the system) at night. A failed queue closes what it claimed.
* **The node only finishes what was claimed for it.** Unknown, finished,
  unqueued and other runs' rows are ignored; it never claims and never
  derives a boundary; it closes its rows FAILED on an exception and raises,
  so the WorkflowRun shows FAILED as well.
* **Not billed.** The dispatcher never goes through the Workflows API's
  priced door.
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.forum import cleanup, dispatch
from toto.forum.models import (ForumChannel, ForumCleanupRun, ForumMember,
                               ForumMessage, ForumRetentionPolicy, RunStatus,
                               TriggeredBy)

User = get_user_model()

_ROOT = tempfile.mkdtemp()


@contextmanager
def worker(task_id="task-1"):
    """A listening worker and a queue that accepts: what `.delay` was asked
    is on the yielded mock, and nothing actually runs."""
    with mock.patch("toto.celery_utils.celery_available", return_value=True), \
         mock.patch("toto.workflows.tasks.start_workflow_run_task.delay") as delay:
        delay.return_value.id = task_id
        yield delay


@contextmanager
def no_worker():
    with mock.patch("toto.celery_utils.celery_available", return_value=False), \
         mock.patch("toto.workflows.tasks.start_workflow_run_task.delay") as delay:
        yield delay


def run_node(workflow_run, test):
    """Run the workflow's node the way the executor does: input only."""
    from toto.workflows.predefined_tasks import run

    with test.captureOnCommitCallbacks(execute=True):
        return run("forum_cleanup", workflow_run.input_data)


@override_settings(FORUM_ATTACHMENT_ROOT=_ROOT, MEDIA_ROOT=_ROOT)
class WorkerBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform
        from toto.people.models import Person

        Platform.objects.get_or_create(
            site_name="Toto", defaults={"author": "Test",
                                        "publication_year": 2026})
        cls.staff = User.objects.create_user(username="s", password="x",
                                             is_staff=True)
        cls.member = User.objects.create_user(username="m", password="x")
        cls.person = Person.objects.create(user=cls.member, display_name="M")
        cls.alpha = ForumChannel.objects.create(name="Alpha", slug="alpha")
        cls.beta = ForumChannel.objects.create(name="Beta", slug="beta")
        for room in (cls.alpha, cls.beta):
            ForumMember.objects.create(channel=room, person=cls.person,
                                       is_active=True)

    def setUp(self):
        self.default = ForumRetentionPolicy.default()
        self.default.retention_days = 30
        self.default.enabled = True
        self.default.save()

    def _message(self, channel, *, days_old=0, body="hi"):
        from toto.forum import store

        row = store.store_message(channel, msg_type="chat_message", body=body,
                                  sender=self.member, sender_name="M")
        ForumMessage.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(days=days_old))
        return row

    def _own(self, channel, *, enabled=True, days=365):
        own = ForumRetentionPolicy.for_channel(channel)
        own.enabled = enabled
        own.retention_days = days
        own.save()
        return own

    def _workflow_run(self, run_or_pk):
        from toto.workflows.models import WorkflowRun

        pk = getattr(run_or_pk, "workflow_run_id", run_or_pk)
        return WorkflowRun.objects.get(pk=pk)


class NoWorkerTests(WorkerBase):
    def test_the_room_button_claims_nothing_and_says_so(self):
        old = self._message(self.alpha, days_old=90)
        self.client.force_login(self.staff)
        with no_worker() as delay:
            response = self.client.post(
                reverse("forum:room_cleanup_run", args=[self.alpha.slug]),
                {"confirm": "DELETE"}, follow=True)
        self.assertFalse(ForumCleanupRun.objects.exists())
        self.assertTrue(ForumMessage.objects.filter(pk=old.pk).exists())
        delay.assert_not_called()
        self.assertContains(response, "No worker is listening")

    def test_the_forum_button_claims_nothing_and_says_so(self):
        self._message(self.alpha, days_old=90)
        self.client.force_login(self.staff)
        with no_worker() as delay:
            response = self.client.post(reverse("forum:cleanup_run"),
                                        {"confirm": "DELETE"}, follow=True)
        self.assertFalse(ForumCleanupRun.objects.exists())
        delay.assert_not_called()
        self.assertContains(response, "No worker is listening")

    def test_without_the_workflow_engine_nothing_is_claimed(self):
        with worker() as delay, \
             mock.patch.object(dispatch, "workflows_installed",
                               return_value=False):
            with self.assertRaises(dispatch.CannotQueue):
                dispatch.start_room(self.alpha, self.staff)
        self.assertFalse(ForumCleanupRun.objects.exists())
        delay.assert_not_called()

    def test_the_pages_say_cleanup_needs_the_worker(self):
        """No inline run to fall back on, so no button either."""
        self._message(self.alpha, days_old=90)
        self.client.force_login(self.staff)
        with no_worker():
            for url in (reverse("forum:cleanup"),
                        reverse("forum:room_settings", args=[self.alpha.slug])):
                with self.subTest(url=url):
                    response = self.client.get(url)
                    self.assertContains(response, 'data-testid="cleanup-needs-worker"')
                    self.assertNotContains(response, "start a worker")
                    self.assertNotContains(response, '@click="confirming = true"')


class RoomButtonTests(WorkerBase):
    def test_one_row_one_workflow_run_started_by_the_staff_member(self):
        self.client.force_login(self.staff)
        with worker("task-77") as delay:
            response = self.client.post(
                reverse("forum:room_cleanup_run", args=[self.alpha.slug]),
                {"confirm": "DELETE"}, follow=True)
        run = ForumCleanupRun.objects.get()
        self.assertEqual(run.status, RunStatus.RUNNING)
        self.assertEqual(run.channel, self.alpha)
        self.assertEqual(run.triggered_by, TriggeredBy.MANUAL)
        self.assertEqual(run.triggered_by_user, self.staff)
        self.assertIsNotNone(run.workflow_run_id)
        self.assertEqual(run.task_id, "task-77")

        workflow_run = self._workflow_run(run)
        self.assertEqual(workflow_run.workflow.slug, "forum-cleanup")
        self.assertEqual(workflow_run.started_by, self.staff)
        self.assertEqual(workflow_run.input_data, {"data": {
            "cleanup_run_ids": [run.pk], "workflow_run_id": workflow_run.pk}})
        delay.assert_called_once_with(workflow_run.pk)
        self.assertContains(response, "Cleanup started")

    def test_the_node_finishes_only_this_room(self):
        old_alpha = self._message(self.alpha, days_old=90)
        old_beta = self._message(self.beta, days_old=90)
        with worker():
            run = dispatch.start_room(self.alpha, self.staff)
        out = run_node(self._workflow_run(run), self)

        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.SUCCESS)
        self.assertEqual(out["data"]["finished"], 1)
        self.assertEqual(out["data"]["messages_deleted"], 1)
        self.assertFalse(ForumMessage.objects.filter(pk=old_alpha.pk).exists())
        self.assertTrue(ForumMessage.objects.filter(pk=old_beta.pk).exists())
        self.assertFalse(cleanup.in_flight(self.alpha))

    def test_a_second_press_while_one_is_queued_is_refused(self):
        self.client.force_login(self.staff)
        with worker():
            dispatch.start_room(self.alpha, self.staff)
            response = self.client.post(
                reverse("forum:room_cleanup_run", args=[self.alpha.slug]),
                {"confirm": "DELETE"}, follow=True)
        self.assertEqual(ForumCleanupRun.objects.count(), 1)
        self.assertContains(response, "already running")

    def test_a_failed_queue_closes_the_claim(self):
        """Otherwise `in_flight` would refuse every cleanup of this room until
        the stuck-run sweeper came by, hours later."""
        with mock.patch("toto.celery_utils.celery_available", return_value=True), \
             mock.patch("toto.workflows.tasks.start_workflow_run_task.delay",
                        side_effect=OSError("broker down")):
            with self.assertRaises(dispatch.CannotQueue):
                dispatch.start_room(self.alpha, self.staff)
        run = ForumCleanupRun.objects.get()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn("broker down", run.error)
        self.assertFalse(cleanup.in_flight(self.alpha))
        self.assertEqual(self._workflow_run(run).status, "failed")

    def test_the_page_shows_the_message_when_the_queue_fails(self):
        self.client.force_login(self.staff)
        with mock.patch("toto.celery_utils.celery_available", return_value=True), \
             mock.patch("toto.workflows.tasks.start_workflow_run_task.delay",
                        side_effect=OSError("broker down")):
            response = self.client.post(
                reverse("forum:room_cleanup_run", args=[self.alpha.slug]),
                {"confirm": "DELETE"}, follow=True)
        self.assertContains(response, "could not be handed to the worker")


class ForumButtonTests(WorkerBase):
    def test_it_claims_what_the_night_runs_as_one_workflow_run(self):
        self._own(self.alpha, enabled=True, days=365)
        self._own(self.beta, enabled=False)
        self.client.force_login(self.staff)
        with worker() as delay:
            self.client.post(reverse("forum:cleanup_run"), {"confirm": "DELETE"})

        runs = list(ForumCleanupRun.objects.order_by("pk"))
        # Alpha's own pass and the platform pass; Beta's dial is off.
        self.assertEqual([r.channel for r in runs], [self.alpha, None])
        self.assertEqual({r.triggered_by for r in runs}, {TriggeredBy.MANUAL})
        self.assertEqual({r.triggered_by_user for r in runs}, {self.staff})
        self.assertEqual(len({r.workflow_run_id for r in runs}), 1)
        workflow_run = self._workflow_run(runs[0])
        self.assertEqual(workflow_run.started_by, self.staff)
        self.assertEqual(workflow_run.input_data["data"]["cleanup_run_ids"],
                         [r.pk for r in runs])
        delay.assert_called_once_with(workflow_run.pk)

    def test_running_it_keeps_each_rooms_own_rule(self):
        self._own(self.alpha, enabled=True, days=365)
        kept = self._message(self.alpha, days_old=90)
        alpha_old = self._message(self.alpha, days_old=400)
        swept = self._message(self.beta, days_old=90)
        with worker():
            runs = dispatch.start_forum(self.staff)
        run_node(self._workflow_run(runs[0]), self)

        self.assertTrue(ForumMessage.objects.filter(pk=kept.pk).exists())
        self.assertFalse(ForumMessage.objects.filter(pk=alpha_old.pk).exists())
        self.assertFalse(ForumMessage.objects.filter(pk=swept.pk).exists())
        self.assertEqual(
            set(ForumCleanupRun.objects.values_list("status", flat=True)),
            {RunStatus.SUCCESS})

    def test_a_person_runs_the_platform_pass_even_with_its_dial_off(self):
        """Off stops the schedule, not somebody who pressed the button."""
        self.default.enabled = False
        self.default.save()
        swept = self._message(self.beta, days_old=90)
        with worker():
            runs = dispatch.start_forum(self.staff)
        run_node(self._workflow_run(runs[0]), self)
        self.assertFalse(ForumMessage.objects.filter(pk=swept.pk).exists())

    def test_it_refuses_whole_while_any_cleanup_is_live(self):
        with worker():
            dispatch.start_room(self.alpha, self.staff)
            with self.assertRaises(cleanup.CleanupInProgress):
                dispatch.start_forum(self.staff)
        self.assertEqual(ForumCleanupRun.objects.count(), 1)


class NodeTests(WorkerBase):
    """The node finishes rows that were claimed for its own run, and nothing
    else — whatever its input says."""

    def _claimed(self, channel=None):
        with worker():
            if channel is None:
                return dispatch.start_forum(self.staff)[-1]
            return dispatch.start_room(channel, self.staff)

    def test_a_row_never_queued_is_ignored(self):
        old = self._message(self.alpha, days_old=90)
        bare = cleanup.trigger(triggered_by=TriggeredBy.MANUAL,
                               channel=self.alpha)
        queued = self._claimed(self.beta)
        workflow_run = self._workflow_run(queued)
        forged = {"data": {"cleanup_run_ids": [bare.pk, queued.pk],
                           "workflow_run_id": workflow_run.pk}}
        from toto.workflows.predefined_tasks import run

        out = run("forum_cleanup", forged)
        bare.refresh_from_db()
        self.assertEqual(bare.status, RunStatus.RUNNING)
        self.assertTrue(ForumMessage.objects.filter(pk=old.pk).exists())
        self.assertEqual(out["data"]["finished"], 1)
        self.assertEqual(out["data"]["ignored"], 1)

    def test_another_runs_row_is_ignored(self):
        old = self._message(self.alpha, days_old=90)
        theirs = self._claimed(self.alpha)
        mine = self._claimed(self.beta)
        from toto.workflows.predefined_tasks import run

        out = run("forum_cleanup", {"data": {
            "cleanup_run_ids": [theirs.pk],
            "workflow_run_id": mine.workflow_run_id}})
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, RunStatus.RUNNING)
        self.assertTrue(ForumMessage.objects.filter(pk=old.pk).exists())
        self.assertEqual(out["data"]["finished"], 0)

    def test_without_its_own_run_id_it_finishes_nothing(self):
        """`workflow_run_id=None` would otherwise match every unqueued row."""
        bare = cleanup.trigger(triggered_by=TriggeredBy.MANUAL,
                               channel=self.alpha)
        from toto.workflows.predefined_tasks import run

        for data in ({"cleanup_run_ids": [bare.pk]},
                     {"cleanup_run_ids": [bare.pk], "workflow_run_id": None},
                     {"cleanup_run_ids": [bare.pk], "workflow_run_id": True},
                     {"cleanup_run_ids": [bare.pk], "workflow_run_id": "1"}):
            with self.subTest(data=data):
                out = run("forum_cleanup", {"data": data})
                self.assertEqual(out["data"]["finished"], 0)
        bare.refresh_from_db()
        self.assertEqual(bare.status, RunStatus.RUNNING)

    def test_a_finished_row_and_unknown_ids_are_ignored(self):
        claimed = self._claimed(self.alpha)
        workflow_run = self._workflow_run(claimed)
        run_node(workflow_run, self)
        claimed.refresh_from_db()
        finished_at = claimed.finished_at

        from toto.workflows.predefined_tasks import run

        out = run("forum_cleanup", {"data": {
            "cleanup_run_ids": [claimed.pk, 987654, "x", None],
            "workflow_run_id": workflow_run.pk}})
        claimed.refresh_from_db()
        self.assertEqual(claimed.finished_at, finished_at)
        self.assertEqual(out["data"]["finished"], 0)

    def test_it_never_claims_or_runs_the_schedule_from_its_input(self):
        self._message(self.alpha, days_old=90)
        from toto.workflows.predefined_tasks import run

        with mock.patch.object(cleanup, "run_scheduled") as scheduled, \
             mock.patch.object(cleanup, "claim_passes") as claim, \
             mock.patch.object(cleanup, "trigger") as trigger:
            run("forum_cleanup", {"data": {"cleanup_run_ids": [],
                                           "workflow_run_id": 1}})
            run("forum_cleanup", {})
        scheduled.assert_not_called()
        claim.assert_not_called()
        trigger.assert_not_called()
        self.assertFalse(ForumCleanupRun.objects.exists())

    def test_the_boundary_is_the_claims_not_the_inputs(self):
        recent = self._message(self.alpha, days_old=1)
        claimed = self._claimed(self.alpha)
        workflow_run = self._workflow_run(claimed)
        data = dict(workflow_run.input_data["data"])
        data.update({"boundary": timezone.now().isoformat(),
                     "retention_days": 0})
        from toto.workflows.predefined_tasks import run

        run("forum_cleanup", {"data": data})
        self.assertTrue(ForumMessage.objects.filter(pk=recent.pk).exists())

    def test_an_exception_closes_every_row_failed_and_raises(self):
        self._own(self.alpha, enabled=True, days=30)
        with worker():
            runs = dispatch.start_forum(self.staff)
        self.assertEqual(len(runs), 2)
        workflow_run = self._workflow_run(runs[0])
        from toto.workflows.predefined_tasks import run

        with mock.patch.object(cleanup, "run_cleanup",
                               side_effect=RuntimeError("disk gone")):
            with self.assertRaises(RuntimeError):
                run("forum_cleanup", workflow_run.input_data)
        for row in ForumCleanupRun.objects.all():
            self.assertEqual(row.status, RunStatus.FAILED)
        self.assertFalse(cleanup.in_flight())

    def test_the_executor_marks_the_workflow_run_failed(self):
        from toto.workflows.services.executor import WorkflowExecutor

        claimed = self._claimed(self.alpha)
        workflow_run = self._workflow_run(claimed)
        with mock.patch.object(cleanup, "run_cleanup",
                               side_effect=RuntimeError("disk gone")):
            WorkflowExecutor().start(workflow_run)
        workflow_run.refresh_from_db()
        claimed.refresh_from_db()
        self.assertEqual(workflow_run.status, "failed")
        self.assertEqual(claimed.status, RunStatus.FAILED)

    def test_the_executor_completes_a_good_run(self):
        from toto.workflows.services.executor import WorkflowExecutor

        self._message(self.alpha, days_old=90)
        claimed = self._claimed(self.alpha)
        workflow_run = self._workflow_run(claimed)
        with self.captureOnCommitCallbacks(execute=True):
            WorkflowExecutor().start(workflow_run)
        workflow_run.refresh_from_db()
        claimed.refresh_from_db()
        self.assertEqual(workflow_run.status, "completed")
        self.assertEqual(claimed.status, RunStatus.SUCCESS)
        self.assertEqual(claimed.messages_deleted, 1)

    def test_an_exhausted_deadline_stops_part_way_and_says_so(self):
        self._own(self.alpha, enabled=True, days=30)
        with worker():
            runs = dispatch.start_forum(self.staff)
        workflow_run = self._workflow_run(runs[0])
        with mock.patch.object(cleanup, "WORKER_DEADLINE_SECONDS", 0):
            out = run_node(workflow_run, self)
        self.assertEqual(
            set(ForumCleanupRun.objects.values_list("status", flat=True)),
            {RunStatus.PARTIAL})
        self.assertIn({"skipped": "out_of_time", "channel": None},
                      out["data"]["runs"])
        self.assertFalse(cleanup.in_flight())

    def test_a_room_deleted_after_the_claim_is_not_swept_as_the_forum(self):
        """Its FK goes NULL; run as it stood, the row would read as
        forum-wide and sweep every room at this room's boundary."""
        doomed = ForumChannel.objects.create(name="Gone", slug="gone")
        other_old = self._message(self.beta, days_old=90)
        claimed = self._claimed(doomed)
        doomed.delete()
        run_node(self._workflow_run(claimed), self)
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, RunStatus.SUCCESS)
        self.assertTrue(ForumMessage.objects.filter(pk=other_old.pk).exists())


class BeatTests(WorkerBase):
    def test_the_beat_dispatches_one_run_covering_the_passes(self):
        from toto.forum.tasks import forum_cleanup

        self._own(self.alpha, enabled=True, days=365)
        self._own(self.beta, enabled=False)
        # The beat runs ON a worker: it must not ask whether one is listening.
        with mock.patch("toto.celery_utils.celery_available",
                        return_value=False), \
             mock.patch("toto.workflows.tasks.start_workflow_run_task.delay") as delay:
            delay.return_value.id = "beat-task"
            out = forum_cleanup()

        runs = list(ForumCleanupRun.objects.order_by("pk"))
        self.assertEqual([r.channel for r in runs], [self.alpha, None])
        self.assertEqual({r.triggered_by for r in runs}, {TriggeredBy.BEAT})
        self.assertEqual({r.triggered_by_user for r in runs}, {None})
        workflow_run = self._workflow_run(runs[0])
        self.assertIsNone(workflow_run.started_by)
        self.assertEqual({r.workflow_run_id for r in runs}, {workflow_run.pk})
        self.assertEqual({r.task_id for r in runs}, {"beat-task"})
        self.assertEqual(out["workflow_run"], workflow_run.pk)
        delay.assert_called_once_with(workflow_run.pk)

    def test_the_beat_does_nothing_while_every_dial_is_off(self):
        from toto.forum.tasks import forum_cleanup
        from toto.workflows.models import WorkflowRun

        self.default.enabled = False
        self.default.save()
        self._own(self.alpha, enabled=False)
        with worker() as delay:
            out = forum_cleanup()
        self.assertEqual(out["skipped"], "disabled")
        self.assertFalse(ForumCleanupRun.objects.exists())
        self.assertFalse(WorkflowRun.objects.exists())
        delay.assert_not_called()

    def test_the_beat_skips_a_pass_already_in_flight(self):
        from toto.forum.tasks import forum_cleanup

        self._own(self.alpha, enabled=True, days=365)
        self._own(self.beta, enabled=True, days=365)
        with worker():
            busy = dispatch.start_room(self.alpha, self.staff)
            out = forum_cleanup()
        new = ForumCleanupRun.objects.exclude(pk=busy.pk)
        # Beta's pass runs; Alpha's own is busy, and the platform pass waits
        # because a live room sweep overlaps it.
        self.assertEqual([r.channel for r in new], [self.beta])
        self.assertIn({"skipped": "in_progress", "channel": "alpha"},
                      out["passes"])
        self.assertIn({"skipped": "in_progress", "channel": None},
                      out["passes"])

    def test_without_the_workflow_engine_the_beat_finishes_on_this_worker(self):
        from toto.forum.tasks import forum_cleanup

        old = self._message(self.beta, days_old=90)
        with mock.patch.object(dispatch, "workflows_installed",
                               return_value=False), \
             self.captureOnCommitCallbacks(execute=True):
            out = forum_cleanup()
        self.assertTrue(out["runs"])
        self.assertFalse(ForumMessage.objects.filter(pk=old.pk).exists())


class WorkflowSeedTests(WorkerBase):
    def test_ingress_seeds_the_workflow_without_full(self):
        from django.core.management import call_command

        from toto.workflows.models import Workflow, WorkflowNode

        call_command("ingress_forum", mode="realistic")
        call_command("ingress_forum", mode="realistic")
        workflow = Workflow.objects.get(slug="forum-cleanup")
        self.assertEqual(workflow.name, "Forum cleanup")
        self.assertEqual(list(workflow.nodes.values_list(
            "node_type", "task_name")),
            [(WorkflowNode.PREDEFINED_TASK, "forum_cleanup")])

    def test_a_missing_node_heals_itself(self):
        from toto.forum.workflow import ensure_cleanup_workflow

        workflow = ensure_cleanup_workflow()
        workflow.nodes.all().delete()
        self.assertEqual(ensure_cleanup_workflow().nodes.count(), 1)

    def test_the_node_is_dispatch_only(self):
        from toto.workflows.predefined_tasks import (dispatch_only_tasks,
                                                     is_dispatch_only)

        from toto.forum.workflow import ensure_cleanup_workflow

        self.assertTrue(is_dispatch_only("forum_cleanup"))
        self.assertEqual(dispatch_only_tasks(ensure_cleanup_workflow()),
                         ["forum_cleanup"])

    def test_nobody_starts_it_by_hand_from_the_workflows_api(self):
        from toto.workflows.models import WorkflowRun

        from toto.forum.workflow import ensure_cleanup_workflow

        workflow = ensure_cleanup_workflow()
        claimed = cleanup.trigger(triggered_by=TriggeredBy.MANUAL,
                                  channel=self.alpha)
        for user in (self.staff, self.member):
            with self.subTest(user=user.username), worker() as delay:
                self.client.force_login(user)
                response = self.client.post(
                    reverse("workflows:api_run_list", args=[workflow.pk]),
                    {"input_data": {"data": {
                        "cleanup_run_ids": [claimed.pk]}}},
                    content_type="application/json")
                self.assertEqual(response.status_code, 403)
                if user.is_staff:
                    # A member may be stopped earlier by a host's own staff
                    # gate; the library's sentence is asserted for both in
                    # toto.workflows.tests_dispatch_only.
                    self.assertIn("never by hand", response.json()["detail"])
                delay.assert_not_called()
        self.assertFalse(WorkflowRun.objects.exists())
        claimed.refresh_from_db()
        self.assertIsNone(claimed.workflow_run_id)

    def test_its_runs_are_still_listed_and_viewable(self):
        with worker():
            claimed = dispatch.start_room(self.alpha, self.staff)
        self.client.force_login(self.staff)
        workflow_run = self._workflow_run(claimed)
        listed = self.client.get(reverse(
            "workflows:api_run_list", args=[workflow_run.workflow_id]))
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([r["id"] for r in listed.json()], [workflow_run.pk])
        page = self.client.get(reverse("workflows:workflow_run_detail",
                                       args=[workflow_run.pk]))
        self.assertEqual(page.status_code, 200)


class LinkTests(WorkerBase):
    """Each cleanup row links to its workflow run, from both desks."""

    def test_the_room_tab_and_the_forum_page_link_to_the_workflow_run(self):
        with worker():
            room_run = dispatch.start_room(self.alpha, self.staff)
        run_node(self._workflow_run(room_run), self)
        with worker():
            wide = dispatch.start_forum(self.staff)[-1]
        run_node(self._workflow_run(wide), self)

        self.client.force_login(self.staff)
        with worker():
            room_page = self.client.get(
                reverse("forum:room_settings", args=[self.alpha.slug]))
            forum_page = self.client.get(reverse("forum:cleanup"))
        self.assertContains(room_page, reverse(
            "workflows:workflow_run_detail", args=[room_run.workflow_run_id]))
        self.assertContains(forum_page, reverse(
            "workflows:workflow_run_detail", args=[wide.workflow_run_id]))
        # The forum page lists forum-wide runs only.
        self.assertNotContains(forum_page, reverse(
            "workflows:workflow_run_detail", args=[room_run.workflow_run_id]))

    def test_a_run_without_a_workflow_shows_no_link(self):
        from toto.forum.views import _workflow_run_url

        self.assertEqual(_workflow_run_url(None), "")
        with mock.patch("django.apps.apps.is_installed", return_value=False):
            self.assertEqual(_workflow_run_url(5), "")

    def test_the_admin_shows_the_workflow_run(self):
        from django.contrib import admin

        from toto.forum.admin import ForumCleanupRunAdmin

        self.assertIn("workflow_run_id", ForumCleanupRunAdmin.list_display)
        model_admin = admin.site._registry[ForumCleanupRun]
        self.assertIn("workflow_run_id", model_admin.readonly_fields)


class NotBilledTests(WorkerBase):
    def test_dispatching_records_no_usage_and_no_charge(self):
        from toto.workflows.models import WorkflowUsageEvent

        with worker(), \
             mock.patch("toto.quota.charge.charge") as charge, \
             mock.patch("toto.quota.charge.check_funds") as check_funds, \
             mock.patch("toto.quota.record_usage") as record_usage:
            dispatch.start_room(self.alpha, self.staff)
            dispatch.start_scheduled()
        self.assertFalse(WorkflowUsageEvent.objects.exists())
        charge.assert_not_called()
        check_funds.assert_not_called()
        record_usage.assert_not_called()

    def test_the_quota_is_not_consulted_either(self):
        with worker(), mock.patch("toto.quota.check_quota") as check_quota:
            dispatch.start_forum(self.staff)
        check_quota.assert_not_called()


class SweeperTests(WorkerBase):
    def test_the_sweeper_knows_the_task_id(self):
        import toto.forum.sweeps  # noqa: F401 — registers on import
        from toto.quota.sweeps import all_policies

        policy = next(p for p in all_policies()
                      if p.model_label == "forum.ForumCleanupRun")
        self.assertEqual(policy.task_id_field, "task_id")
        self.assertEqual(policy.closer, "toto.forum.cleanup.fail_run")
