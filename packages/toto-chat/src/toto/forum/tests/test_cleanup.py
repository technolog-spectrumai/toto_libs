"""Cleanup: what goes, what stays, and how the schedule runs it (stage 69).

The owner, 2026-10-07: "Admins can clean channels manually or configure
scheduled cleanup by retention age in Settings. Reuse existing
scheduled-task infrastructure. Remove eligible messages, polls and their
forum-owned attachments; preserve unrelated community-bucket files."

    manage.py test toto.forum.tests.test_cleanup
"""

from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.core.files.base import ContentFile
from django.utils import timezone

from toto.forum import channels, cleanup, dispatch, tasks
from toto.forum.models import (ChannelPoll, ForumChannel, ForumChannelKey, ForumCleanupRun,
                               ForumMessage, ForumSettings, PollBallot, PollChoice,
                               RunStatus, TriggeredBy)
from toto.forum.testing import JPEG, ForumCase, client_of, send_json, upload
from toto.vault.models import VaultFile
from toto.vault.storage_backends import persist_upload


@contextmanager
def worker(task_id="task-1"):
    """A listening worker and a queue that accepts: what ``.delay`` was
    asked is on the yielded mock, and nothing runs."""
    with mock.patch("toto.celery_utils.celery_available", return_value=True), \
            mock.patch("toto.workflows.tasks.start_workflow_run_task.delay") as delay:
        delay.return_value.id = task_id
        yield delay


@contextmanager
def no_worker():
    with mock.patch("toto.celery_utils.celery_available", return_value=False), \
            mock.patch("toto.workflows.tasks.start_workflow_run_task.delay") as delay:
        yield delay


@contextmanager
def working_queue():
    """A queue whose worker runs the workflow at once, through the engine:
    ``.delay`` executes ``start_workflow_run_task`` in this process."""
    from toto.workflows.tasks import start_workflow_run_task

    def at_once(pk):
        return start_workflow_run_task.apply(args=[pk], throw=True)

    with mock.patch("toto.celery_utils.celery_available", return_value=True), \
            mock.patch("toto.workflows.tasks.start_workflow_run_task.delay",
                       side_effect=at_once) as delay:
        yield delay


class CleanupCase(ForumCase):
    def age(self, row_id, model=ForumMessage, **delta):
        """Make a row as old as ``delta`` says."""
        when = timezone.now() - timedelta(**delta)
        model.objects.filter(pk=row_id).update(created_at=when)
        return when

    def message(self, text="hello", days=0, image=None, user=None, community=None):
        answer = self.say(user or self.member, text, image=image, community=community)
        self.assertEqual(answer.status_code, 201, answer.content)
        row_id = answer.json()["message"]["id"]
        if days:
            self.age(row_id, days=days)
        return ForumMessage.objects.get(pk=row_id)

    def poll(self, days=0, voters=(), **more):
        answer = self.open_poll(self.member, **more)
        self.assertEqual(answer.status_code, 201, answer.content)
        poll = answer.json()["poll"]
        for voter in voters:
            vote = send_json(client_of(voter), self.url("poll_vote", poll["id"]),
                             {"choice": poll["choices"][0]["id"]})
            self.assertEqual(vote.status_code, 200, vote.content)
        if days:
            self.age(poll["id"], ChannelPoll, days=days)
        return ChannelPoll.objects.get(pk=poll["id"])

    def sweep(self, days=None, channel=None, deadline=None):
        """Claim a cleanup and finish it here, as the worker's node does;
        the vault unlinks its bytes when the transaction commits."""
        run = cleanup.claim(boundary=cleanup.boundary_for(days), retention_days=days or 0,
                            triggered_by=TriggeredBy.MANUAL, user=self.admin, channel=channel)
        with self.captureOnCommitCallbacks(execute=True):
            cleanup.finish([run], deadline_seconds=deadline)
        run.refresh_from_db()
        return run

    def texts(self, user=None):
        return [m["text"] for m in self.feed(user or self.member).json()["messages"]]


class RuleTests(CleanupCase):
    def test_messages_made_before_the_boundary_go_and_newer_ones_stay(self):
        old = self.message("a year ago", days=400)
        self.message("last month", days=40)
        self.message("today")
        run = self.sweep(days=365)
        self.assertEqual(run.status, RunStatus.SUCCESS)
        self.assertFalse(ForumMessage.objects.filter(pk=old.pk).exists())
        self.assertEqual(self.texts(), ["last month", "today"])
        self.assertEqual((run.messages_deleted, run.channels_touched), (1, 1))
        self.assertIsNotNone(run.finished_at)

    def test_the_boundary_exactly(self):
        """Made strictly before the boundary goes; made at it stays."""
        at = self.message("at the boundary")
        before = self.message("an instant before")
        run = cleanup.claim(boundary=timezone.now(), retention_days=0,
                            triggered_by=TriggeredBy.MANUAL)
        ForumMessage.objects.filter(pk=at.pk).update(created_at=run.boundary)
        ForumMessage.objects.filter(pk=before.pk).update(
            created_at=run.boundary - timedelta(microseconds=1))
        cleanup.finish([run])
        self.assertTrue(ForumMessage.objects.filter(pk=at.pk).exists())
        self.assertFalse(ForumMessage.objects.filter(pk=before.pk).exists())

    def test_an_image_goes_from_the_vault_row_and_bytes(self):
        old = self.message("with a picture", days=400, image=upload())
        new = self.message("a new picture", image=upload(JPEG, "new.jpg", "image/jpeg"))
        stored = VaultFile.all_objects.get(pk=old.attachment_id)
        storage, name = stored.file.storage, stored.file.name
        self.assertTrue(storage.exists(name))
        run = self.sweep(days=365)
        self.assertFalse(VaultFile.all_objects.filter(pk=stored.pk).exists())
        self.assertFalse(storage.exists(name))
        self.assertEqual(run.attachments_deleted, 1)
        self.assertEqual(run.bytes_freed, stored.file_size_bytes)
        # The newer message keeps its picture, and it still opens.
        kept = VaultFile.all_objects.get(pk=new.attachment_id)
        self.assertTrue(kept.file.storage.exists(kept.file.name))
        self.assertEqual(client_of(self.second).get(
            self.url("message_image", new.pk)).status_code, 200)

    def test_an_unrelated_file_in_the_bucket_stays(self):
        """Somebody's own file in the channel's bucket is not the forum's:
        a cleanup of everything leaves its row and its bytes."""
        unrelated = VaultFile(owner=self.second, title="minutes.txt", key="minutes",
                              file_type="text", bucket=self.channel.bucket)
        persist_upload(unrelated, ContentFile(b"the minutes", name="minutes.txt"))
        aged = VaultFile(owner=self.member, title="old-plan.txt", key="old-plan",
                         file_type="text", bucket=self.channel.bucket)
        persist_upload(aged, ContentFile(b"the plan", name="old-plan.txt"))
        VaultFile.all_objects.filter(pk=aged.pk).update(
            uploaded_at=timezone.now() - timedelta(days=900))
        self.message("with a picture", days=400, image=upload())
        self.message("another", image=upload(JPEG, "o.jpg", "image/jpeg"))
        run = self.sweep()                                   # everything
        self.assertEqual((run.messages_deleted, run.attachments_deleted), (2, 2))
        left = VaultFile.all_objects.filter(bucket=self.channel.bucket).order_by("pk")
        self.assertEqual([f.pk for f in left], [unrelated.pk, aged.pk])
        self.assertEqual(left[0].file.read(), b"the minutes")
        self.assertEqual(left[1].file.read(), b"the plan")

    def test_polls_go_with_their_options_and_answers_open_or_closed(self):
        old_open = self.poll(days=400, voters=(self.member, self.second))
        old_final = self.poll(days=400, voters=(self.head,), title="Final?",
                              revisability="final")
        old_closed = self.poll(days=400, voters=(self.senior,), title="Closed?")
        old_closed.close()
        new = self.poll(voters=(self.member,), title="Now?")
        run = self.sweep(days=365)
        gone = [old_open.pk, old_final.pk, old_closed.pk]
        self.assertFalse(ChannelPoll.objects.filter(pk__in=gone).exists())
        self.assertFalse(PollChoice.objects.filter(poll_id__in=gone).exists())
        self.assertFalse(PollBallot.objects.filter(poll_id__in=gone).exists())
        self.assertEqual((run.polls_deleted, run.ballots_deleted), (3, 4))
        self.assertTrue(ChannelPoll.objects.filter(pk=new.pk).exists())
        self.assertEqual(PollChoice.objects.filter(poll=new).count(), 2)
        self.assertEqual(PollBallot.objects.filter(poll=new).count(), 1)
        self.assertEqual([p["title"] for p in self.feed(self.member).json()["polls"]], ["Now?"])

    def test_tombstones_go_too(self):
        removed = self.message("said and withdrawn")
        client_of(self.member).post(self.url("message_remove", removed.pk))
        self.age(removed.pk, days=400)
        poll = self.poll()
        send_json(client_of(self.member), self.url("poll_remove", poll.pk))
        self.age(poll.pk, ChannelPoll, days=400)
        fresh = self.message("withdrawn today")
        client_of(self.member).post(self.url("message_remove", fresh.pk))
        run = self.sweep(days=365)
        self.assertFalse(ForumMessage.objects.filter(pk=removed.pk).exists())
        self.assertFalse(ChannelPoll.objects.filter(pk=poll.pk).exists())
        self.assertTrue(ForumMessage.objects.filter(pk=fresh.pk).exists())
        self.assertEqual((run.messages_deleted, run.polls_deleted), (1, 1))

    def test_an_image_the_vault_had_lost_already_is_counted_as_missing(self):
        old = self.message("with a picture", days=400, image=upload())
        VaultFile.all_objects.get(pk=old.attachment_id).delete()
        run = self.sweep(days=365)
        self.assertEqual((run.messages_deleted, run.attachments_deleted, run.blobs_missing),
                         (1, 0, 1))

    def test_everything_leaves_the_channel_its_key_and_its_bucket(self):
        self.message("one", days=3)
        self.message("two", image=upload())
        self.poll(voters=(self.second,))
        bucket = self.channel.bucket_id
        run = self.sweep()
        self.assertEqual((run.messages_deleted, run.polls_deleted, run.retention_days), (2, 1, 0))
        self.assertFalse(ForumMessage.objects.exists())
        self.assertFalse(ChannelPoll.objects.exists())
        channel = ForumChannel.objects.get(pk=self.channel.pk)
        self.assertEqual(channel.bucket_id, bucket)
        self.assertTrue(ForumChannelKey.objects.filter(channel=channel).exists())
        self.assertTrue(self.member_person.communities.filter(pk=self.guild.pk).exists())
        # The channel goes on: the next message takes the next number.
        after = self.message("after the cleanup")
        self.assertEqual(after.number, channel.last_seq + 1)
        self.assertEqual(self.texts(), ["after the cleanup"])

    def test_one_channel_or_all(self):
        other = channels.ensure_channel(self.other)
        mine = self.message("the guild's", days=400)
        theirs = self.message("the other's", days=400, user=self.outsider, community=self.other)
        run = self.sweep(days=365, channel=self.channel)
        self.assertEqual((run.channel, run.channel_name), (self.channel, "Guild"))
        self.assertFalse(ForumMessage.objects.filter(pk=mine.pk).exists())
        self.assertTrue(ForumMessage.objects.filter(pk=theirs.pk).exists())
        self.assertIsNone(ForumChannel.objects.get(pk=other.pk).purged_before)
        wide = self.sweep(days=365)
        self.assertIsNone(wide.channel)
        self.assertFalse(ForumMessage.objects.filter(pk=theirs.pk).exists())
        self.assertIsNotNone(ForumChannel.objects.get(pk=other.pk).purged_before)

    def test_the_usage_records_of_removed_messages_stay(self):
        from toto.forum.models import ForumUsageEvent

        self.message("billed and gone", days=400)
        events = ForumUsageEvent.objects.count()
        self.assertEqual(events, 1)
        self.sweep(days=365)
        self.assertEqual(ForumUsageEvent.objects.count(), events)

    def test_the_record_holds_counts_and_nothing_said(self):
        self.message("the harbour at dawn", days=400, image=upload())
        self.poll(days=400, title="Harbour or hills?")
        run = self.sweep(days=365)
        kept = " ".join(str(getattr(run, field.name)) for field in run._meta.fields)
        self.assertNotIn("harbour", kept.lower())
        self.assertEqual((run.triggered_by, run.triggered_by_user, run.retention_days),
                         (TriggeredBy.MANUAL, self.admin, 365))


class OpenPageTests(CleanupCase):
    """What an open page is told: the feed's ``purged_before`` and cursor."""

    def test_the_channel_s_boundary_and_event_counter_move(self):
        self.message("old", days=400)
        self.message("new")
        before = ForumChannel.objects.get(pk=self.channel.pk)
        self.assertIsNone(before.purged_before)
        cursor = self.feed(self.member).json()["cursor"]
        run = self.sweep(days=365)
        after = ForumChannel.objects.get(pk=self.channel.pk)
        self.assertEqual(after.purged_before, run.boundary)
        self.assertEqual(after.last_seq, before.last_seq + 1)
        changed = self.feed(self.member, after=cursor).json()
        self.assertEqual(changed["purged_before"], run.boundary.isoformat())
        self.assertEqual(changed["cursor"], cursor + 1)
        self.assertEqual(changed["messages"], [])
        first = self.feed(self.member).json()
        self.assertEqual(first["purged_before"], run.boundary.isoformat())
        self.assertEqual([m["text"] for m in first["messages"]], ["new"])
        older = self.feed(self.member, before=10 ** 6).json()
        self.assertEqual([m["text"] for m in older["messages"]], ["new"])

    def test_a_pass_that_finds_nothing_still_moves_the_boundary_once(self):
        self.message("new")
        first = self.sweep(days=365)
        channel = ForumChannel.objects.get(pk=self.channel.pk)
        self.assertEqual(channel.purged_before, first.boundary)
        self.assertEqual((first.messages_deleted, first.channels_touched), (0, 0))
        # An earlier boundary never moves it back, and moves nothing.
        seq = channel.last_seq
        self.sweep(days=700)
        channel.refresh_from_db()
        self.assertEqual((channel.purged_before, channel.last_seq), (first.boundary, seq))

    def test_in_chunks_the_boundary_moves_with_the_last_one(self):
        for n in range(5):
            self.message(f"old {n}", days=400)
        self.message("new")
        with mock.patch.object(cleanup, "DELETE_CHUNK", 2):
            run = self.sweep(days=365)
        self.assertEqual((run.status, run.messages_deleted), (RunStatus.SUCCESS, 5))
        self.assertEqual(self.texts(), ["new"])

    def test_out_of_time_stops_between_chunks_and_the_next_run_continues(self):
        for n in range(5):
            self.message(f"old {n}", days=400)
        seq = ForumChannel.objects.get(pk=self.channel.pk).last_seq
        # The module's clock alone: the run starts at 0, the first chunk is
        # asked at 1, and by the second the ten seconds are long gone.
        ticks = iter([0, 0, 0, 1])
        clock = SimpleNamespace(monotonic=lambda: next(ticks, 99))
        with mock.patch.object(cleanup, "DELETE_CHUNK", 2), \
                mock.patch.object(cleanup, "time", clock):
            partial = self.sweep(days=365, deadline=10)
        self.assertEqual(partial.status, RunStatus.PARTIAL)
        self.assertEqual(partial.messages_deleted, 2)
        self.assertTrue(partial.error)
        channel = ForumChannel.objects.get(pk=self.channel.pk)
        # Not finished: the open pages are not told yet.
        self.assertEqual((channel.purged_before, channel.last_seq), (None, seq))
        self.assertFalse(cleanup.in_flight())
        done = self.sweep(days=365)
        self.assertEqual((done.status, done.messages_deleted), (RunStatus.SUCCESS, 3))
        self.assertIsNotNone(ForumChannel.objects.get(pk=self.channel.pk).purged_before)


class ClaimTests(CleanupCase):
    def test_a_second_cleanup_is_refused_while_one_is_live(self):
        other = channels.ensure_channel(self.other)
        run = cleanup.claim(boundary=timezone.now(), retention_days=0,
                            triggered_by=TriggeredBy.MANUAL, channel=self.channel)
        self.assertTrue(cleanup.in_flight(self.channel))
        self.assertFalse(cleanup.in_flight(other))
        with self.assertRaises(cleanup.CleanupInProgress):
            cleanup.claim(boundary=timezone.now(), retention_days=0,
                          triggered_by=TriggeredBy.MANUAL, channel=self.channel)
        with self.assertRaises(cleanup.CleanupInProgress):       # a wide one covers it
            cleanup.claim(boundary=timezone.now(), retention_days=0,
                          triggered_by=TriggeredBy.MANUAL)
        elsewhere = cleanup.claim(boundary=timezone.now(), retention_days=0,
                                  triggered_by=TriggeredBy.MANUAL, channel=other)
        cleanup.finish([run, elsewhere])
        self.assertFalse(cleanup.in_flight())

    def test_a_run_whose_channel_was_deleted_sweeps_nothing_else(self):
        other = channels.ensure_channel(self.other)
        survivor = self.message("the guild's", days=400)
        run = cleanup.claim(boundary=cleanup.boundary_for(365), retention_days=365,
                            triggered_by=TriggeredBy.MANUAL, channel=other)
        other.delete()
        run.refresh_from_db()
        self.assertIsNone(run.channel_id)
        cleanup.finish([run])
        run.refresh_from_db()
        self.assertEqual((run.status, run.messages_deleted), (RunStatus.SUCCESS, 0))
        self.assertTrue(ForumMessage.objects.filter(pk=survivor.pk).exists())

    def test_a_run_that_raises_is_closed_failed_and_raises(self):
        self.message("old", days=400)
        run = cleanup.claim(boundary=cleanup.boundary_for(365), retention_days=365,
                            triggered_by=TriggeredBy.MANUAL)
        with mock.patch.object(cleanup, "_remove_messages", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                cleanup.finish([run])
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn("OSError", run.error)
        self.assertFalse(cleanup.in_flight())
        self.assertEqual(ForumMessage.objects.count(), 1)

    def test_the_stuck_run_sweep_knows_the_record(self):
        from toto.quota.sweeps import all_policies

        policy = next(p for p in all_policies() if p.model_label == "forum.ForumCleanupRun")
        self.assertEqual(policy.closer, "toto.forum.cleanup.fail_run")
        self.assertEqual(policy.active_values, ("pending", "running"))
        run = cleanup.claim(boundary=timezone.now(), retention_days=0,
                            triggered_by=TriggeredBy.BEAT)
        cleanup.fail_run(run, "the worker was killed")
        run.refresh_from_db()
        self.assertEqual((run.status, run.error), (RunStatus.FAILED, "the worker was killed"))
        self.assertFalse(cleanup.in_flight())

    def test_the_preview_counts_what_would_go_and_removes_nothing(self):
        self.message("old", days=400, image=upload())
        self.message("new")
        self.poll(days=400, voters=(self.second,))
        counted = cleanup.preview(cleanup.boundary_for(365))
        self.assertEqual((counted["messages"], counted["images"], counted["polls"],
                          counted["ballots"], counted["total_messages"]), (1, 1, 1, 1, 2))
        self.assertGreater(counted["bytes"], 0)
        self.assertEqual(ForumMessage.objects.count(), 2)


class ScheduleTests(CleanupCase):
    """The beat's task, called directly."""

    def retention(self, days=30, on=True):
        row = ForumSettings.current()
        row.retention_enabled, row.retention_days = on, days
        row.save()
        return row

    def test_the_beat_entry_and_the_worker_s_module(self):
        from toto.registry import TASK_MODULES
        from toto.schedules import beat_schedule

        self.assertNotIn("forum-cleanup", beat_schedule())
        entry = beat_schedule(forum_cleanup=True)["forum-cleanup"]
        self.assertEqual(entry["task"], "toto.forum.tasks.forum_cleanup")
        self.assertEqual(tasks.forum_cleanup.name, entry["task"])
        self.assertIn("toto.forum", TASK_MODULES)

    def test_switched_off_it_does_nothing(self):
        from toto.workflows.models import WorkflowRun

        old = self.message("a year ago", days=4000)
        ForumSettings.objects.all().delete()
        for on_file in (False, True):
            if on_file:
                self.retention(days=30, on=False)
            with worker() as delay:
                self.assertEqual(tasks.forum_cleanup(), {"skipped": "disabled"})
            delay.assert_not_called()
            self.assertFalse(ForumCleanupRun.objects.exists())
            self.assertFalse(WorkflowRun.objects.exists())
        self.assertTrue(ForumMessage.objects.filter(pk=old.pk).exists())
        self.assertIsNone(ForumChannel.objects.get(pk=self.channel.pk).purged_before)

    def test_a_platform_nobody_configured_gets_no_settings_row_from_the_beat(self):
        ForumSettings.objects.all().delete()
        with worker():
            tasks.forum_cleanup()
        self.assertFalse(ForumSettings.objects.exists())

    def test_switched_on_the_night_runs_end_to_end(self):
        """The task, the claim, one run of the "Forum cleanup" workflow
        through the engine, the node, the rows and the bytes."""
        from toto.workflows.models import WorkflowRun

        self.retention(days=30)
        old = self.message("two months ago", days=60, image=upload())
        old_poll = self.poll(days=60, voters=(self.second,))
        self.message("last week", days=7)
        stored = VaultFile.all_objects.get(pk=old.attachment_id)
        with working_queue() as delay, self.captureOnCommitCallbacks(execute=True):
            answer = tasks.forum_cleanup()
        run = ForumCleanupRun.objects.get()
        workflow_run = WorkflowRun.objects.get()
        self.assertEqual(answer, {"workflow_run": workflow_run.pk, "cleanup_runs": [run.pk]})
        delay.assert_called_once_with(workflow_run.pk)
        self.assertEqual(workflow_run.workflow.slug, "forum-cleanup")
        self.assertIsNone(workflow_run.started_by)
        self.assertEqual(workflow_run.status, "completed")
        self.assertEqual((run.status, run.triggered_by, run.triggered_by_user,
                          run.retention_days, run.channel, run.workflow_run_id),
                         (RunStatus.SUCCESS, TriggeredBy.BEAT, None, 30, None, workflow_run.pk))
        self.assertAlmostEqual(run.boundary, timezone.now() - timedelta(days=30),
                               delta=timedelta(minutes=1))
        self.assertEqual((run.messages_deleted, run.attachments_deleted, run.polls_deleted,
                          run.ballots_deleted), (1, 1, 1, 1))
        self.assertFalse(ForumMessage.objects.filter(pk=old.pk).exists())
        self.assertFalse(ChannelPoll.objects.filter(pk=old_poll.pk).exists())
        self.assertFalse(VaultFile.all_objects.filter(pk=stored.pk).exists())
        self.assertFalse(stored.file.storage.exists(stored.file.name))
        self.assertEqual(self.texts(), ["last week"])
        self.assertEqual(ForumChannel.objects.get(pk=self.channel.pk).purged_before, run.boundary)

    def test_the_night_is_queued_not_run_in_the_task(self):
        from toto.workflows.models import WorkflowRun

        self.retention(days=30)
        old = self.message("two months ago", days=60)
        with worker("task-9") as delay:
            answer = tasks.forum_cleanup()
        run = ForumCleanupRun.objects.get()
        workflow_run = WorkflowRun.objects.get()
        delay.assert_called_once_with(workflow_run.pk)
        self.assertEqual(answer["cleanup_runs"], [run.pk])
        self.assertEqual((run.status, run.task_id), (RunStatus.RUNNING, "task-9"))
        self.assertEqual(workflow_run.input_data, {"data": {
            "cleanup_run_ids": [run.pk], "workflow_run_id": workflow_run.pk}})
        self.assertTrue(ForumMessage.objects.filter(pk=old.pk).exists())
        # A second night while the first is still live claims nothing.
        with worker() as again:
            self.assertEqual(tasks.forum_cleanup(), {"skipped": "in_progress"})
        again.assert_not_called()
        self.assertEqual(ForumCleanupRun.objects.count(), 1)

    def test_without_a_workflow_engine_the_worker_finishes_it_itself(self):
        self.retention(days=30)
        old = self.message("two months ago", days=60)
        with mock.patch.object(dispatch, "workflows_installed", return_value=False):
            answer = tasks.forum_cleanup()
            self.retention(on=False)
            self.assertEqual(tasks.forum_cleanup(), {"skipped": "disabled"})
        self.assertEqual(answer["runs"][0]["messages"], 1)
        self.assertFalse(ForumMessage.objects.filter(pk=old.pk).exists())
        self.assertEqual(ForumCleanupRun.objects.get().status, RunStatus.SUCCESS)

    def test_a_queue_that_refuses_closes_what_was_claimed(self):
        self.retention(days=30)
        with mock.patch("toto.workflows.tasks.start_workflow_run_task.delay",
                        side_effect=ConnectionError("no broker")):
            with self.assertRaises(dispatch.CannotQueue):
                tasks.forum_cleanup()
        run = ForumCleanupRun.objects.get()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn("no broker", run.error)
        self.assertFalse(cleanup.in_flight())

    def test_the_node_finishes_only_what_was_claimed_for_it(self):
        from toto.workflows.predefined_tasks import is_dispatch_only, run as run_node

        self.assertTrue(is_dispatch_only("forum_cleanup"))
        old = self.message("two months ago", days=60)
        unqueued = cleanup.claim(boundary=cleanup.boundary_for(30), retention_days=30,
                                 triggered_by=TriggeredBy.MANUAL)
        # No run id of its own, another run's id, an unknown record, junk:
        for data in ({"cleanup_run_ids": [unqueued.pk]},
                     {"cleanup_run_ids": [unqueued.pk], "workflow_run_id": 777},
                     {"cleanup_run_ids": [unqueued.pk], "workflow_run_id": True},
                     {"cleanup_run_ids": [999999, "x", None], "workflow_run_id": 777},
                     {"cleanup_run_ids": "all", "workflow_run_id": 777}):
            with self.subTest(data=data):
                out = run_node("forum_cleanup", {"data": data})
                self.assertEqual(out["data"]["finished"], 0)
        unqueued.refresh_from_db()
        self.assertEqual(unqueued.status, RunStatus.RUNNING)
        self.assertTrue(ForumMessage.objects.filter(pk=old.pk).exists())
        ForumCleanupRun.objects.filter(pk=unqueued.pk).update(workflow_run_id=777)
        out = run_node("forum_cleanup", {"data": {"cleanup_run_ids": [unqueued.pk],
                                                  "workflow_run_id": 777}})
        self.assertEqual((out["data"]["finished"], out["data"]["messages_deleted"]), (1, 1))
        # A finished record is not run again.
        again = run_node("forum_cleanup", {"data": {"cleanup_run_ids": [unqueued.pk],
                                                    "workflow_run_id": 777}})
        self.assertEqual(again["data"]["finished"], 0)

    def test_the_workflow_is_made_once(self):
        from toto.forum.workflow import ensure_cleanup_workflow
        from toto.workflows.models import Workflow

        first = ensure_cleanup_workflow()
        first.nodes.all().delete()
        second = ensure_cleanup_workflow()                  # the node is healed
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Workflow.objects.filter(slug="forum-cleanup").count(), 1)
        self.assertEqual(list(second.nodes.values_list("task_name", flat=True)),
                         ["forum_cleanup"])
