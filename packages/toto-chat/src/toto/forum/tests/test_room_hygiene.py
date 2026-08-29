"""Forum hygiene, per room.

The whole point of this module is the word BETWEEN: every claim here is about
one room not touching another. A retention period set in Alpha must not shorten
Beta's history; a sweep of Alpha must not count or delete a message in Beta; and
an archive taken from Alpha's Settings tab must not contain a single byte of
Beta — because that archive is a file somebody hands to the people in a room,
and a leak in it is not recoverable by fixing the code afterwards.

The second theme is the two RACES this design can lose. A room's own sweep and
a forum-wide sweep overlap by definition, so the in-flight refusal has to be
asymmetric: a room refuses while its own OR a wide run is live, and a wide run
refuses while anything at all is live. And the archive must never be built from
numbers the page computed earlier — it surveys, then streams, and the survey is
what refuses.

Attachments go into a temporary root, exactly as `test_cleanup.py` does and for
the same reason: without it these tests delete files out of the running
server's tree.
"""

from __future__ import annotations

import io
import tempfile
import zipfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.forum import cleanup, export
from toto.forum.models import (ForumChannel, ForumCleanupRun, ForumMember,
                               ForumMessage, ForumRetentionPolicy, PollBallot,
                               PollChoice, RoomPoll, RunStatus, TriggeredBy)

User = get_user_model()

_ROOT = tempfile.mkdtemp()


def _drain(response) -> bytes:
    """Collect a streamed body, sync or async.

    The download view yields from an `async def` generator so ASGI streams for
    real instead of materialising the archive in memory. Under the sync test
    client that arrives as an ASYNC iterator, which `b"".join` cannot take —
    hence the branch. `thread_sensitive=True` inside the view means the ORM
    work lands back on this thread, so there is no
    `SynchronousOnlyOperation` to dodge.
    """
    content = response.streaming_content
    if hasattr(content, "__aiter__"):
        from asgiref.sync import async_to_sync

        async def collect():
            return b"".join([chunk async for chunk in content])

        return async_to_sync(collect)()
    return b"".join(content)


@override_settings(FORUM_ATTACHMENT_ROOT=_ROOT, MEDIA_ROOT=_ROOT)
class RoomHygieneBase(TestCase):
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
        self.default = ForumRetentionPolicy.current()
        self.default.retention_days = 30
        self.default.enabled = True
        self.default.save()

    # -- fixtures ---------------------------------------------------------

    def _message(self, room, *, days_old=0, attach=False, body="hi",
                 attach_bytes=None):
        from toto.forum import store

        kwargs = {}
        if attach:
            payload = attach_bytes if attach_bytes is not None else b"x" * 12
            kwargs = {"attachment": ContentFile(payload, name="a.png"),
                      "attachment_name": "a.png",
                      "attachment_mime": "image/png",
                      "attachment_size": len(payload)}
        row = store.store_message(
            room, msg_type=("image_message" if attach else "chat_message"),
            body=body, sender=self.member, sender_name="M", **kwargs)
        when = timezone.now() - timedelta(days=days_old)
        ForumMessage.objects.filter(pk=row.pk).update(created_at=when)
        row.refresh_from_db()
        return row

    def _poll(self, room, *, days_old=0, with_vote=True):
        poll = RoomPoll.objects.create(channel=room, title="T", slug=f"p{room.pk}-{days_old}",
                                       created_by=self.member)
        choice = PollChoice.objects.create(poll=poll, label="A", position=0)
        if with_vote:
            PollBallot.objects.create(poll=poll, choice=choice,
                                      voter=self.member)
        when = timezone.now() - timedelta(days=days_old)
        RoomPoll.objects.filter(pk=poll.pk).update(created_at=when)
        poll.refresh_from_db()
        return poll

    def _run(self, channel=None):
        run = cleanup.trigger(triggered_by=TriggeredBy.MANUAL, channel=channel)
        with self.captureOnCommitCallbacks(execute=True):
            cleanup.run_cleanup(run)
        run.refresh_from_db()
        return run


class PolicyResolutionTests(RoomHygieneBase):
    """Which dial governs a room, and what changes when it gets its own."""

    def test_a_room_without_an_override_follows_the_platform(self):
        self.assertEqual(ForumRetentionPolicy.current(self.alpha).pk,
                         self.default.pk)
        self.assertTrue(ForumRetentionPolicy.current(self.alpha).is_default)

    def test_an_override_governs_only_its_own_room(self):
        own = ForumRetentionPolicy.for_channel(self.alpha)
        own.retention_days = 400
        own.save()
        self.assertEqual(ForumRetentionPolicy.current(self.alpha).pk, own.pk)
        self.assertEqual(ForumRetentionPolicy.current(self.beta).pk,
                         self.default.pk)

    def test_one_override_per_room_is_enforced_by_the_database(self):
        """The constraint is PARTIAL, and this is why it has to be.

        A plain unique on a nullable column permits any number of NULLs — that
        is SQL, not a Django quirk — so it would allow a second platform
        default while claiming to prevent duplicates.
        """
        from django.db import IntegrityError, transaction

        ForumRetentionPolicy.for_channel(self.alpha)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ForumRetentionPolicy.objects.create(channel=self.alpha,
                                                    retention_days=10)

    def test_the_platform_default_is_a_single_row(self):
        first = ForumRetentionPolicy.default()
        self.assertIsNone(first.channel_id)
        self.assertEqual(ForumRetentionPolicy.default().pk, first.pk)

    def test_asking_for_the_current_policy_does_not_mint_an_override(self):
        """`current()` reads; only `for_channel()` writes.

        If reading created a row, merely opening the Settings tab would stop
        the room following the platform — silently, and for good.
        """
        ForumRetentionPolicy.current(self.alpha)
        self.assertFalse(
            ForumRetentionPolicy.objects.filter(channel=self.alpha).exists())


class ScopedPreviewTests(RoomHygieneBase):
    def test_a_room_preview_counts_only_that_room(self):
        self._message(self.alpha, days_old=90)
        self._message(self.alpha, days_old=90)
        self._message(self.beta, days_old=90)

        self.assertEqual(cleanup.preview(channel=self.alpha)["messages"], 2)
        self.assertEqual(cleanup.preview(channel=self.beta)["messages"], 1)
        self.assertEqual(cleanup.preview()["messages"], 3)

    def test_a_room_preview_uses_that_rooms_own_boundary(self):
        own = ForumRetentionPolicy.for_channel(self.alpha)
        own.retention_days = 365
        own.save()
        self._message(self.alpha, days_old=90)
        self._message(self.beta, days_old=90)

        self.assertEqual(cleanup.preview(channel=self.alpha)["messages"], 0)
        self.assertEqual(cleanup.preview(channel=self.beta)["messages"], 1)

    def test_the_totals_are_scoped_too(self):
        """`total_messages` is the denominator on the page. Left unscoped it
        would read "3 of 40 in this room" for a room holding four."""
        self._message(self.alpha, days_old=1)
        self._message(self.beta, days_old=1)
        self.assertEqual(cleanup.preview(channel=self.alpha)["total_messages"], 1)


class ScopedDeletionTests(RoomHygieneBase):
    def test_a_room_sweep_leaves_every_other_room_untouched(self):
        old_alpha = self._message(self.alpha, days_old=90)
        old_beta = self._message(self.beta, days_old=90)

        run = self._run(channel=self.alpha)

        self.assertEqual(run.messages_deleted, 1)
        self.assertFalse(ForumMessage.objects.filter(pk=old_alpha.pk).exists())
        self.assertTrue(ForumMessage.objects.filter(pk=old_beta.pk).exists())

    def test_the_run_records_which_room_it_swept(self):
        self._message(self.alpha, days_old=90)
        run = self._run(channel=self.alpha)
        self.assertEqual(run.channel_id, self.alpha.pk)
        self.assertEqual(run.channel_name, "Alpha")

    def test_a_run_survives_the_deletion_of_its_room(self):
        """`channel` is SET_NULL and `channel_name` is text for this reason:
        the history of what was deleted must outlive the room it came from."""
        self._message(self.alpha, days_old=90)
        run = self._run(channel=self.alpha)
        self.alpha.delete()
        run.refresh_from_db()
        self.assertIsNone(run.channel_id)
        self.assertEqual(run.channel_name, "Alpha")

    def test_attachment_bytes_of_the_swept_room_leave_the_disk(self):
        import os

        alpha_file = self._message(self.alpha, days_old=90, attach=True)
        beta_file = self._message(self.beta, days_old=90, attach=True)
        alpha_path = alpha_file.attachment.path
        beta_path = beta_file.attachment.path
        self.assertTrue(os.path.exists(alpha_path))

        self._run(channel=self.alpha)

        self.assertFalse(os.path.exists(alpha_path))
        self.assertTrue(os.path.exists(beta_path),
                        "a room sweep deleted another room's bytes")

    def test_polls_and_their_votes_go_with_the_room_sweep(self):
        old = self._poll(self.alpha, days_old=90)
        kept = self._poll(self.beta, days_old=90)

        run = self._run(channel=self.alpha)

        self.assertEqual(run.polls_deleted, 1)
        self.assertEqual(run.ballots_deleted, 1)
        self.assertFalse(RoomPoll.objects.filter(pk=old.pk).exists())
        self.assertFalse(PollBallot.objects.filter(poll_id=old.pk).exists())
        self.assertTrue(RoomPoll.objects.filter(pk=kept.pk).exists())

    def test_nothing_is_exempt_not_even_a_message_everybody_wants_kept(self):
        """There is no pin, no star and no exemption filter anywhere in the
        delete path, and there must never be one: retention that spares what
        somebody marked important is not retention."""
        self._message(self.alpha, days_old=90, body="IMPORTANT — read me")
        run = self._run(channel=self.alpha)
        self.assertEqual(run.messages_deleted, 1)


class ScheduledSweepTests(RoomHygieneBase):
    def test_a_room_with_a_longer_period_is_spared_by_the_platform_sweep(self):
        """The failure this prevents: a room asking to keep a year of history
        losing it to a platform dial set to thirty days."""
        own = ForumRetentionPolicy.for_channel(self.alpha)
        own.retention_days = 365
        own.enabled = True
        own.save()
        kept = self._message(self.alpha, days_old=90)
        swept = self._message(self.beta, days_old=90)

        with self.captureOnCommitCallbacks(execute=True):
            cleanup.run_scheduled()

        self.assertTrue(ForumMessage.objects.filter(pk=kept.pk).exists())
        self.assertFalse(ForumMessage.objects.filter(pk=swept.pk).exists())

    def test_a_room_that_turned_cleanup_off_keeps_everything(self):
        own = ForumRetentionPolicy.for_channel(self.alpha)
        own.enabled = False
        own.save()
        kept = self._message(self.alpha, days_old=900)

        with self.captureOnCommitCallbacks(execute=True):
            cleanup.run_scheduled()

        self.assertTrue(ForumMessage.objects.filter(pk=kept.pk).exists())

    def test_a_disabled_platform_dial_still_lets_a_room_sweep_itself(self):
        self.default.enabled = False
        self.default.save()
        own = ForumRetentionPolicy.for_channel(self.alpha)
        own.enabled = True
        own.retention_days = 30
        own.save()
        swept = self._message(self.alpha, days_old=90)
        kept = self._message(self.beta, days_old=900)

        with self.captureOnCommitCallbacks(execute=True):
            cleanup.run_scheduled()

        self.assertFalse(ForumMessage.objects.filter(pk=swept.pk).exists())
        self.assertTrue(ForumMessage.objects.filter(pk=kept.pk).exists())


class WideRunRespectsOverridesTests(RoomHygieneBase):
    """The forum-wide MANUAL run defers to room overrides, like the nightly one.

    For one day it did not: `run_cleanup` only excluded override rooms when
    `run_scheduled` passed the list in, and the wide desk's button passed
    nothing — so the platform dial overruled every room that had asked to keep
    its history, from the one entry point a person presses by hand.
    """

    def test_a_wide_manual_run_spares_rooms_with_their_own_dial(self):
        own = ForumRetentionPolicy.for_channel(self.alpha)
        own.retention_days = 365
        own.enabled = True
        own.save()
        kept = self._message(self.alpha, days_old=90)
        swept = self._message(self.beta, days_old=90)

        self._run(channel=None)

        self.assertTrue(ForumMessage.objects.filter(pk=kept.pk).exists(),
                        "the wide manual run overruled a room's own retention")
        self.assertFalse(ForumMessage.objects.filter(pk=swept.pk).exists())

    def test_a_disabled_override_also_shields_its_room_from_the_wide_run(self):
        """Disabled means "nothing expires here", not "back to the platform".
        Going back to the platform is the Reset button, which deletes the row.
        """
        own = ForumRetentionPolicy.for_channel(self.alpha)
        own.enabled = False
        own.save()
        kept = self._message(self.alpha, days_old=900)
        kept_poll = self._poll(self.alpha, days_old=900)

        self._run(channel=None)

        self.assertTrue(ForumMessage.objects.filter(pk=kept.pk).exists())
        self.assertTrue(RoomPoll.objects.filter(pk=kept_poll.pk).exists(),
                        "the wide run deleted a shielded room's poll")

    def test_the_wide_preview_counts_what_the_wide_run_would_delete(self):
        """The desk's numbers and the button's deletions come from one rule —
        a preview that counted the override rooms would promise more deletion
        than pressing the button delivers."""
        own = ForumRetentionPolicy.for_channel(self.alpha)
        own.retention_days = 365
        own.enabled = True
        own.save()
        self._message(self.alpha, days_old=90)
        self._message(self.beta, days_old=90)
        self._poll(self.alpha, days_old=90)
        self._poll(self.beta, days_old=90)

        wide = cleanup.preview()
        self.assertEqual(wide["messages"], 1)
        self.assertEqual(wide["polls"], 1)


class SharedDeadlineTests(RoomHygieneBase):
    def test_the_nightly_deadline_is_one_budget_not_one_per_pass(self):
        """With N enabled overrides a per-pass budget could run the task N
        times past what tasks.py promised celery. An exhausted budget skips
        the remaining passes and says so; every chunk already committed, so
        the next night resumes."""
        for room in (self.alpha, self.beta):
            own = ForumRetentionPolicy.for_channel(room)
            own.enabled = True
            own.save()

        with self.captureOnCommitCallbacks(execute=True):
            out = cleanup.run_scheduled(deadline_seconds=0)

        self.assertTrue(out["runs"], "no passes were even attempted")
        self.assertIn({"skipped": "out_of_time", "channel": None},
                      [r for r in out["runs"] if "skipped" in r])


class RaceTests(RoomHygieneBase):
    """The in-flight refusal, which is asymmetric on purpose."""

    def _live(self, channel=None):
        return ForumCleanupRun.objects.create(
            status=RunStatus.RUNNING, triggered_by=TriggeredBy.MANUAL,
            channel=channel,
            channel_name=channel.name if channel else "",
            boundary=timezone.now(), retention_days=30)

    def test_two_different_rooms_may_sweep_at_once(self):
        self._live(self.alpha)
        self.assertFalse(cleanup.in_flight(self.beta))
        cleanup.trigger(triggered_by=TriggeredBy.MANUAL, channel=self.beta)

    def test_a_room_refuses_while_its_own_sweep_is_live(self):
        self._live(self.alpha)
        self.assertTrue(cleanup.in_flight(self.alpha))
        with self.assertRaises(cleanup.CleanupInProgress):
            cleanup.trigger(triggered_by=TriggeredBy.MANUAL,
                            channel=self.alpha)

    def test_a_room_refuses_while_a_forum_wide_sweep_is_live(self):
        """The wide one covers the narrow one, so they would delete the same
        rows and the second would count what was already gone."""
        self._live(None)
        self.assertTrue(cleanup.in_flight(self.alpha))
        with self.assertRaises(cleanup.CleanupInProgress):
            cleanup.trigger(triggered_by=TriggeredBy.MANUAL,
                            channel=self.alpha)

    def test_a_forum_wide_sweep_refuses_while_any_room_is_sweeping(self):
        self._live(self.alpha)
        self.assertTrue(cleanup.in_flight())
        with self.assertRaises(cleanup.CleanupInProgress):
            cleanup.trigger(triggered_by=TriggeredBy.MANUAL)


class ScopedExportTests(RoomHygieneBase):
    """The archive is a file that leaves the platform. It leaks or it does not."""

    def _zip(self, channel=None):
        """The archive, opened as a ZIP.

        Every assertion below reads through this rather than off the raw
        stream, because the entries are DEFLATED: a `assertNotIn(b"secret",
        raw_bytes)` would pass on an archive that carried the secret, and a
        leak test that cannot fail is worse than none.
        """
        plan = export.survey(actor="s", channel=channel)
        buf = io.BytesIO()
        for chunk in export.stream_archive(plan):
            buf.write(chunk)
        buf.seek(0)
        return zipfile.ZipFile(buf)

    def test_a_room_archive_holds_that_room_and_no_other(self):
        self._message(self.alpha, body="alpha-secret")
        self._message(self.beta, body="beta-secret")

        archive = self._zip(self.alpha)
        blob = b"".join(archive.read(n) for n in archive.namelist())

        self.assertIn(b"alpha-secret", blob)
        self.assertNotIn(b"beta-secret", blob,
                         "a room archive carried another room's messages")
        self.assertNotIn(b"Beta", blob,
                         "a room archive named another room")

    def test_a_room_archive_carries_no_other_rooms_files(self):
        """Asserted by COUNT and by BYTES, never by filename.

        Attachment members are content-addressed (`attachments/<sha256>-…`) —
        no room slug ever appears in a member name, so a name-based assertion
        passes vacuously forever. The two fixtures also carry DIFFERENT bytes,
        because identical files collapse onto one member by design and would
        make the count prove nothing.
        """
        import hashlib

        self._message(self.alpha, attach=True, attach_bytes=b"alpha-bytes-1")
        self._message(self.beta, attach=True, attach_bytes=b"beta-bytes-02")

        archive = self._zip(self.alpha)
        members = [n for n in archive.namelist()
                   if n.startswith("attachments/")]
        self.assertEqual(len(members), 1,
                         f"expected exactly alpha's one file: {members}")
        self.assertEqual(archive.read(members[0]), b"alpha-bytes-1")
        beta_sha = hashlib.sha256(b"beta-bytes-02").hexdigest()
        self.assertFalse(any(beta_sha in n for n in archive.namelist()),
                         "beta's content hash reached the archive")

    def test_the_survey_is_what_scopes_it_not_the_renderer(self):
        """Scoping later would still have measured, and named in the manifest,
        rooms the reader may not see."""
        self._message(self.alpha)
        self._message(self.beta)
        plan = export.survey(actor="s", channel=self.alpha)
        self.assertEqual([r.channel_id for r in plan.rooms], [self.alpha.pk])
        self.assertEqual(plan.total_messages, 1)

    def test_the_manifest_and_front_page_declare_the_narrow_scope(self):
        """A per-room archive is HANDED to that room's members. For a day its
        manifest said `all-rooms-operator-export` and its front page said
        "contains every room, including private ones" — a file that overstates
        both what it holds and who must have made it."""
        import json

        self._message(self.alpha)
        archive = self._zip(self.alpha)
        data = json.loads(archive.read("manifest.json"))
        self.assertEqual(data["scope"], "single-room-export:alpha")
        index = archive.read("index.html").decode()
        self.assertNotIn("every room", index)
        self.assertIn("alpha", index)

        wide = json.loads(self._zip().read("manifest.json"))
        self.assertEqual(wide["scope"], "all-rooms-operator-export")

    def test_the_filename_names_the_room(self):
        name = export.export_filename(channel=self.alpha)
        self.assertTrue(name.startswith("forum-alpha-"))
        self.assertTrue(name.endswith(".zip"))
        self.assertFalse(
            export.export_filename().startswith("forum-alpha-"))

    def test_an_empty_room_still_produces_a_readable_archive(self):
        archive = self._zip(self.beta)
        self.assertTrue(archive.namelist())
        self.assertIsNone(archive.testzip())


class SettingsTabTests(RoomHygieneBase):
    """The tab itself: who reaches it, and what its buttons do."""

    def test_the_tab_is_staff_only(self):
        self.client.force_login(self.member)
        response = self.client.get(
            reverse("forum:room_settings", args=[self.alpha.slug]))
        self.assertEqual(response.status_code, 403)

    def test_staff_see_the_tab(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("forum:room_settings", args=[self.alpha.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tab"], "settings")
        self.assertEqual(response.context["channel"], self.alpha)

    def test_the_tab_link_is_hidden_from_members_and_shown_to_staff(self):
        """A tab that always answers 403 is worse than no tab.

        BOTH halves read the Statistics page, never Settings: on the Settings
        page the tab's URL is a prefix of every form action, so asserting it
        there passes with the tab deleted from the strip — a check that cannot
        fail. Statistics renders the same `_room_tabs.html` and contains no
        other settings-URL, which is what makes the staff half real. The staff
        account is enrolled in the room first, because Statistics is
        member-gated and staffhood does not imply membership here.
        """
        from toto.forum.models import ForumMember

        url = reverse("forum:room_settings", args=[self.alpha.slug])
        stats = reverse("forum:room_stats", args=[self.alpha.slug])

        self.client.force_login(self.member)
        member_page = self.client.get(stats)
        self.assertEqual(member_page.status_code, 200)
        self.assertNotContains(member_page, url)

        from toto.people.models import Person

        staff_person = Person.objects.create(user=self.staff,
                                             display_name="S")
        ForumMember.objects.create(channel=self.alpha, person=staff_person,
                                   is_active=True)
        self.client.force_login(self.staff)
        staff_page = self.client.get(stats)
        self.assertEqual(staff_page.status_code, 200)
        self.assertContains(staff_page, url)

    def test_saving_gives_the_room_its_own_setting(self):
        self.client.force_login(self.staff)
        self.client.post(
            reverse("forum:room_retention", args=[self.alpha.slug]),
            {"enabled": "on", "retention_days": "400"})

        own = ForumRetentionPolicy.objects.get(channel=self.alpha)
        self.assertEqual(own.retention_days, 400)
        self.assertEqual(ForumRetentionPolicy.current(self.beta).pk,
                         self.default.pk)
        self.default.refresh_from_db()
        self.assertEqual(self.default.retention_days, 30,
                         "a room's save moved the platform dial")

    def test_a_post_cannot_move_an_override_to_another_room(self):
        """The form has no channel field, and the view overwrites it anyway."""
        self.client.force_login(self.staff)
        self.client.post(
            reverse("forum:room_retention", args=[self.alpha.slug]),
            {"enabled": "on", "retention_days": "77",
             "channel": str(self.beta.pk)})
        self.assertEqual(
            ForumRetentionPolicy.objects.get(channel=self.alpha).retention_days,
            77)
        self.assertFalse(
            ForumRetentionPolicy.objects.filter(channel=self.beta).exists())

    def test_a_rejected_save_does_not_mint_an_override(self):
        """The write happens only after the form validates. `for_channel()`
        CREATES the override row, and an override row — disabled included —
        removes the room from the platform-wide sweep; minting it before
        `is_valid()` meant a typo detached the room silently and for good."""
        self.client.force_login(self.staff)
        self.client.post(
            reverse("forum:room_retention", args=[self.alpha.slug]),
            {"enabled": "on", "retention_days": "not-a-number"})
        self.assertFalse(
            ForumRetentionPolicy.objects.filter(channel=self.alpha).exists(),
            "a rejected save still detached the room from the platform")

    def test_resetting_deletes_the_override_rather_than_disabling_it(self):
        """Those are different states: a disabled override is a room that
        decided to keep everything, and no row is a room that has not
        decided."""
        ForumRetentionPolicy.for_channel(self.alpha)
        self.client.force_login(self.staff)
        self.client.post(
            reverse("forum:room_retention_reset", args=[self.alpha.slug]))
        self.assertFalse(
            ForumRetentionPolicy.objects.filter(channel=self.alpha).exists())
        self.assertTrue(ForumRetentionPolicy.current(self.alpha).is_default)

    def test_the_run_button_needs_the_confirmation_word(self):
        old = self._message(self.alpha, days_old=90)
        self.client.force_login(self.staff)
        self.client.post(
            reverse("forum:room_cleanup_run", args=[self.alpha.slug]),
            {"confirm": "delete"})
        self.assertTrue(ForumMessage.objects.filter(pk=old.pk).exists())
        self.assertFalse(ForumCleanupRun.objects.exists())

    def test_the_run_button_sweeps_only_this_room(self):
        old_alpha = self._message(self.alpha, days_old=90)
        old_beta = self._message(self.beta, days_old=90)
        self.client.force_login(self.staff)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("forum:room_cleanup_run", args=[self.alpha.slug]),
                {"confirm": "DELETE"})
        self.assertFalse(ForumMessage.objects.filter(pk=old_alpha.pk).exists())
        self.assertTrue(ForumMessage.objects.filter(pk=old_beta.pk).exists())

    def test_the_run_endpoint_rederives_its_own_boundary(self):
        """Nothing the preview put on the page is trusted — a cutoff in a form
        field is a cutoff somebody can edit."""
        recent = self._message(self.alpha, days_old=1)
        self.client.force_login(self.staff)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("forum:room_cleanup_run", args=[self.alpha.slug]),
                {"confirm": "DELETE",
                 "boundary": (timezone.now() + timedelta(days=1)).isoformat()})
        self.assertTrue(ForumMessage.objects.filter(pk=recent.pk).exists())

    def test_the_archive_button_streams_this_rooms_zip(self):
        self._message(self.alpha, body="alpha-secret")
        self._message(self.beta, body="beta-secret")
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("forum:room_export_download", args=[self.alpha.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("forum-alpha-", response["Content-Disposition"])
        # Read THROUGH zipfile, never off the raw bytes: the entries are
        # deflated, so `assertNotIn(b"beta-secret", raw)` would pass on an
        # archive that contained it — a leak test that cannot fail is worse
        # than no leak test.
        archive = zipfile.ZipFile(io.BytesIO(_drain(response)))
        self.assertIsNone(archive.testzip())
        text = b"".join(archive.read(n) for n in archive.namelist())
        self.assertIn(b"alpha-secret", text)
        self.assertNotIn(b"beta-secret", text)
        self.assertEqual([n for n in archive.namelist() if "beta" in n], [])

    def test_every_write_endpoint_is_staff_only(self):
        self.client.force_login(self.member)
        for name in ("room_retention", "room_retention_reset",
                     "room_cleanup_run", "room_export_download"):
            with self.subTest(name=name):
                response = self.client.post(
                    reverse(f"forum:{name}", args=[self.alpha.slug]),
                    {"confirm": "DELETE", "retention_days": "1"})
                self.assertEqual(response.status_code, 403)

    def test_a_missing_room_is_a_404_not_a_500(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("forum:room_settings", args=["no-such-room"]))
        self.assertEqual(response.status_code, 404)
