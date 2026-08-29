"""Forum cleanup: the one thing in this app that destroys history.

Two claims carry the most weight and are asserted directly rather than
inferred: **the attachment BYTES leave the disk** (Django does not delete a
FileField's blob when its row goes, so a bulk delete would orphan every one),
and **the run endpoint re-derives its own boundary** instead of trusting what
the preview screen put on the page.

Everything here writes into a temporary attachment root. That only works
because `ForumAttachmentStorage` re-reads the setting; before that fix these
tests would have been deleting files out of the running server's own tree.
"""

from __future__ import annotations

import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.forum import cleanup
from toto.forum.models import (ForumChannel, ForumCleanupRun, ForumMember,
                               ForumMessage, ForumRetentionPolicy, RunStatus,
                               TriggeredBy)

User = get_user_model()

_ROOT = tempfile.mkdtemp()


@override_settings(FORUM_ATTACHMENT_ROOT=_ROOT, MEDIA_ROOT=_ROOT)
# The forum-LEVEL Cleanup and Export desks were removed on 2026-08-29 — both
# operations are per-room now, on each room's Settings tab — so the classes
# that drove `/forum/cleanup/` and `/forum/export/` went with them. Their
# coverage did not: `tests/test_room_hygiene.py` asserts the staff gate, the
# confirmation word, the boundary re-derivation and the archive scoping
# against the room endpoints that replaced them.


class CleanupBase(TestCase):
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
        cls.member_person = Person.objects.create(user=cls.member,
                                                  display_name="M")
        cls.room = ForumChannel.objects.create(name="Alpha", slug="alpha")
        ForumMember.objects.create(channel=cls.room, person=cls.member_person,
                                   is_active=True)

    def setUp(self):
        self.policy = ForumRetentionPolicy.current()
        self.policy.retention_days = 30
        self.policy.enabled = True
        self.policy.save()

    def _message(self, *, days_old=0, attach=False, deleted=False, body="hi"):
        from toto.forum import store

        kwargs = {}
        if attach:
            kwargs = {"attachment": ContentFile(b"x" * 12, name="a.png"),
                      "attachment_name": "a.png",
                      "attachment_mime": "image/png", "attachment_size": 12}
        row = store.store_message(self.room, msg_type=(
            "image_message" if attach else "chat_message"), body=body,
            sender=self.member, sender_name="M", **kwargs)
        when = timezone.now() - timedelta(days=days_old)
        ForumMessage.objects.filter(pk=row.pk).update(created_at=when)
        if deleted:
            ForumMessage.objects.filter(pk=row.pk).update(
                deleted_at=timezone.now())
        row.refresh_from_db()
        return row


class BoundaryTests(CleanupBase):
    def test_the_boundary_is_now_minus_the_retention_period(self):
        now = timezone.now()
        expected = now - timedelta(days=30)
        self.assertAlmostEqual(
            self.policy.boundary(now=now).timestamp(), expected.timestamp(),
            places=3)

    def test_one_derivation_serves_the_page_and_the_run(self):
        """Nothing else computes a cutoff, which is what lets the run
        endpoint re-derive rather than trust a form field."""
        self.assertEqual(cleanup.preview(self.policy)["boundary"].date(),
                         self.policy.boundary().date())


class DeletionTests(CleanupBase):
    def _run(self):
        """Run a sweep with its on_commit callbacks actually executed.

        The blobs are unlinked in `transaction.on_commit` — row first, bytes
        second, the vault's ordering for its stated reason. A TestCase wraps
        everything in a transaction it rolls back, so those callbacks would
        never fire and every byte assertion below would pass while testing
        nothing. This is what makes them real.
        """
        run = cleanup.trigger(triggered_by=TriggeredBy.MANUAL)
        with self.captureOnCommitCallbacks(execute=True):
            cleanup.run_cleanup(run)
        run.refresh_from_db()
        return run

    def test_a_message_older_than_the_boundary_is_gone(self):
        old = self._message(days_old=40)
        self._run()
        self.assertFalse(ForumMessage.objects.filter(pk=old.pk).exists())

    def test_a_message_inside_the_boundary_is_untouched(self):
        recent = self._message(days_old=5)
        self._run()
        self.assertTrue(ForumMessage.objects.filter(pk=recent.pk).exists())

    def test_the_attachment_bytes_leave_the_disk(self):
        """Django does NOT delete a FileField's blob when the row goes, so a
        bulk delete would leave every attachment orphaned and unreferenced."""
        old = self._message(days_old=40, attach=True)
        storage = ForumMessage._meta.get_field("attachment").storage
        name = old.attachment.name
        self.assertTrue(storage.exists(name))
        run = self._run()
        self.assertFalse(storage.exists(name))
        self.assertEqual(run.attachments_deleted, 1)
        self.assertEqual(run.bytes_freed, 12)

    def test_a_soft_deleted_old_message_loses_its_bytes_too(self):
        """The leak this drains: a soft delete has always kept the file
        forever. The sweep filters created_at, not deleted_at."""
        old = self._message(days_old=40, attach=True, deleted=True)
        storage = ForumMessage._meta.get_field("attachment").storage
        name = old.attachment.name
        self._run()
        self.assertFalse(storage.exists(name))
        self.assertFalse(ForumMessage.objects.filter(pk=old.pk).exists())

    def test_a_recent_soft_deleted_message_keeps_its_bytes_for_now(self):
        """Said plainly so the UI does not claim more than this: the leak
        drains only as fast as messages age."""
        row = self._message(days_old=1, attach=True, deleted=True)
        storage = ForumMessage._meta.get_field("attachment").storage
        self._run()
        self.assertTrue(storage.exists(row.attachment.name))

    def test_a_reply_survives_and_loses_only_its_quote(self):
        parent = self._message(days_old=40)
        reply = ForumMessage.objects.create(
            channel=self.room, sender=self.member, sender_name="M",
            body="answering", reply_to=parent)
        run = self._run()
        reply.refresh_from_db()
        self.assertIsNone(reply.reply_to_id)
        self.assertEqual(reply.body, "answering")
        self.assertEqual(run.replies_orphaned, 1)

    def test_rooms_members_and_polls_are_never_touched(self):
        """A room, a membership and a poll are not conversations. Deleting a
        membership would also re-sync the vault whitelist, and vault reads an
        EMPTY allowed_users as 'every authenticated user' — a retention sweep
        must not be able to publish a library."""
        from toto.forum import voting

        poll = voting.open_poll(self.room, self.member, title="Old?",
                                options="A\nB")
        self._message(days_old=40)
        self._run()
        self.assertTrue(ForumChannel.objects.filter(pk=self.room.pk).exists())
        self.assertTrue(ForumMember.objects.filter(channel=self.room).exists())
        self.assertTrue(voting.polls_for(self.room).filter(pk=poll.pk).exists())

    def test_a_missing_blob_is_counted_not_fatal(self):
        old = self._message(days_old=40, attach=True)
        storage = ForumMessage._meta.get_field("attachment").storage
        storage.delete(old.attachment.name)
        run = self._run()
        self.assertEqual(run.status, RunStatus.SUCCESS)
        self.assertEqual(run.blobs_missing, 1)

    def test_it_sweeps_every_room_not_just_one(self):
        other = ForumChannel.objects.create(name="Beta", slug="beta")
        from toto.forum import store

        row = store.store_message(other, msg_type="chat_message", body="old",
                                  sender=self.member, sender_name="M")
        ForumMessage.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(days=40))
        run = self._run()
        self.assertFalse(ForumMessage.objects.filter(pk=row.pk).exists())
        self.assertEqual(run.channels_touched, 1)


class ScheduleTests(CleanupBase):
    def test_nothing_happens_while_the_dial_is_off(self):
        """A destroyer must not arrive armed."""
        self.policy.enabled = False
        self.policy.save(update_fields=["enabled"])
        old = self._message(days_old=40)
        self.assertEqual(cleanup.run_scheduled(), {"skipped": "disabled"})
        self.assertTrue(ForumMessage.objects.filter(pk=old.pk).exists())

    def test_a_fresh_policy_is_off_by_default(self):
        ForumRetentionPolicy.objects.all().delete()
        self.assertFalse(ForumRetentionPolicy.current().enabled)

    def test_a_second_run_is_refused_while_one_is_in_flight(self):
        """Keyed on the RUN rows, not on the policy's last_run_status: a
        killed worker leaves the policy reading RUNNING with nothing to reset
        it, and a refusal keyed on that would block every future run forever.
        (The select_for_update row lock is postgres-only hardening the sqlite
        gate cannot exercise, which is why the refusal must be correct
        without it.)"""
        cleanup.trigger(triggered_by=TriggeredBy.MANUAL)
        with self.assertRaises(cleanup.CleanupInProgress):
            cleanup.trigger(triggered_by=TriggeredBy.MANUAL)

    def test_a_stuck_run_can_be_closed_and_unblocks_the_next(self):
        run = cleanup.trigger(triggered_by=TriggeredBy.BEAT)
        cleanup.fail_run(run, "worker died")
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertFalse(cleanup.in_flight())
        self.assertEqual(ForumRetentionPolicy.current().last_run_status,
                         RunStatus.FAILED)

    def test_the_run_row_records_what_it_destroyed(self):
        self._message(days_old=40, attach=True)
        run = cleanup.trigger(triggered_by=TriggeredBy.BEAT)
        with self.captureOnCommitCallbacks(execute=True):
            cleanup.run_cleanup(run)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.SUCCESS)
        self.assertEqual(run.messages_deleted, 1)
        self.assertEqual(run.retention_days, 30)
        self.assertIsNotNone(run.finished_at)

    def test_a_deadline_stops_it_part_way_and_says_so(self):
        for _n in range(3):
            self._message(days_old=40)
        run = cleanup.trigger(triggered_by=TriggeredBy.MANUAL)
        with mock.patch.object(cleanup, "DELETE_CHUNK", 1), \
             mock.patch.object(cleanup.time, "monotonic",
                               side_effect=[0, 0, 1, 999]):
            cleanup.run_cleanup(run, deadline_seconds=10)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.PARTIAL)
        self.assertTrue(ForumMessage.objects.exists())

    def test_the_task_is_registered_where_beat_can_find_it(self):
        """A beat entry without a TASK_MODULES line is enqueued forever and
        answered with KeyError — the weather bug, verbatim."""
        from toto.registry import TASK_MODULES

        self.assertIn("toto.forum", TASK_MODULES)


class ReservedSlugTests(CleanupBase):
    def test_a_room_cannot_take_one_of_the_forums_own_addresses(self):
        """`/forum/cleanup/` is declared before the `<slug>/` catch-all, so a
        room slugged `cleanup` would be permanently unreachable. `create` and
        `search` have been shadowed since those routes existed."""
        from django.core.exceptions import ValidationError

        for slug in ("cleanup", "create", "search", "api"):
            with self.subTest(slug=slug):
                channel = ForumChannel(name=slug.title(), slug=slug)
                with self.assertRaises(ValidationError):
                    channel.full_clean()

    def test_the_create_view_says_so_rather_than_erroring(self):
        self.client.force_login(self.member)
        self.client.post(reverse("forum:channel_create"), {"name": "Cleanup"})
        self.assertFalse(ForumChannel.objects.filter(slug="cleanup").exists())
