"""Unit tests for poll threads, replies, vault storage and audit snapshots."""

from datetime import timedelta
from tempfile import TemporaryDirectory

from django.test import override_settings
from django.utils import timezone

from toto.forum import cleanup, poll_audit, threads
from toto.forum import channels
from toto.forum.models import ChannelPoll, ForumMessage, ForumPollAudit, PollBallot
from toto.forum.testing import ForumCase, client_of, op, send_json, upload


class ThreadTests(ForumCase):
    @classmethod
    def setUpClass(cls):
        cls.media_dir = TemporaryDirectory()
        cls.media_settings = override_settings(MEDIA_ROOT=cls.media_dir.name)
        cls.media_settings.enable()
        try:
            super().setUpClass()
        except Exception:
            cls.media_settings.disable()
            cls.media_dir.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            cls.media_settings.disable()
            cls.media_dir.cleanup()

    def create(self, user=None, *, title="Where should we meet?", description="Please decide.",
               community=None, **more):
        data = {"title": title, "description": description,
                "options": "Park\nLibrary", "closes_at": (timezone.now() + timedelta(days=2)).isoformat(),
                **more}
        return send_json(client_of(user or self.member),
                         self.url("poll_open", community=community), data)

    def reply(self, user, poll, text="I prefer the park.", image=None, community=None):
        data = {"poll": str(poll.id), "op": op(), "text": text}
        if image is not None:
            data["image"] = image
        return client_of(user).post(self.url("post", community=community), data)

    def test_title_description_deadline_and_daily_limit(self):
        self.assertEqual(self.create(title="x" * 257).status_code, 400)
        self.assertEqual(self.create(description="x" * 2049).status_code, 400)
        self.assertEqual(self.create(closes_at="").status_code, 400)
        made = [self.create(title=f"Question {n}?") for n in range(3)]
        self.assertEqual([response.status_code for response in made], [201] * 3)
        self.assertEqual(self.create().status_code, 400)
        self.assertEqual(self.create(self.second).status_code, 201)
        poll = ChannelPoll.objects.get(pk=made[0].json()["poll"]["id"])
        self.assertTrue(poll.bucket_id)
        self.assertEqual(poll.directory.bucket_id, poll.bucket_id)
        self.assertEqual(made[0].json()["poll"]["description"], "Please decide.")
        self.assertEqual(ForumPollAudit.objects.filter(poll_id=poll.pk, action="create").count(), 1)

    def test_reset_is_required_to_change_a_vote(self):
        poll_id = self.create().json()["poll"]["id"]
        poll = ChannelPoll.objects.get(pk=poll_id)
        first, second = list(poll.choices.all())
        vote = lambda choice: send_json(client_of(self.second), self.url("poll_vote", poll.pk),
                                        {"choice": choice.pk})
        self.assertEqual(vote(first).status_code, 200)
        self.assertEqual(vote(second).status_code, 409)
        self.assertEqual(PollBallot.objects.get(poll=poll, voter=self.second).choice_id, first.pk)
        self.assertEqual(send_json(client_of(self.second),
                                   self.url("poll_reset", poll.pk)).status_code, 200)
        self.assertFalse(PollBallot.objects.filter(poll=poll, voter=self.second).exists())
        self.assertEqual(vote(second).status_code, 200)
        self.assertEqual(PollBallot.objects.get(poll=poll, voter=self.second).choice_id, second.pk)

    def test_daily_limit_is_per_member_and_community(self):
        self.member_person.communities.add(self.other)
        channels.ensure_channel(self.other)
        for n in range(3):
            self.assertEqual(self.create(title=f"Question {n}?").status_code, 201)
        self.assertEqual(self.create().status_code, 400)
        self.assertEqual(self.create(community=self.other).status_code, 201)
        self.assertEqual(self.create(user=self.second).status_code, 201)
        old = ChannelPoll.objects.filter(channel=self.channel, created_by=self.member).first()
        self.assertEqual(send_json(client_of(self.member),
                                   self.url("poll_remove", old.pk)).status_code, 200)
        self.assertEqual(self.create().status_code, 400)
        ForumPollAudit.objects.filter(poll_id=old.pk, action="create").update(
            created_at=timezone.now() - timedelta(days=1))
        self.assertEqual(self.create().status_code, 201)

    def test_admin_can_delete_open_thread_and_image_bucket(self):
        poll = ChannelPoll.objects.get(pk=self.create().json()["poll"]["id"])
        bucket_id = poll.bucket_id
        self.assertEqual(self.reply(self.member, poll, image=upload()).status_code, 201)
        self.assertEqual(send_json(client_of(self.admin),
                                   self.url("poll_remove", poll.pk)).status_code, 200)
        poll.refresh_from_db()
        self.assertIsNotNone(poll.removed_at)
        self.assertIsNone(poll.bucket_id)
        from toto.vault.models import Bucket
        self.assertFalse(Bucket.objects.filter(pk=bucket_id).exists())
        self.assertEqual(ForumPollAudit.objects.filter(poll_id=poll.pk, action="delete").count(), 1)

    def test_replies_are_linear_and_images_use_poll_directory(self):
        poll = ChannelPoll.objects.get(pk=self.create().json()["poll"]["id"])
        first = self.reply(self.member, poll, "First", upload())
        second = self.reply(self.second, poll, "Second")
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        stored = ForumMessage.objects.get(pk=first.json()["message"]["id"])
        self.assertEqual(stored.poll_id, poll.pk)
        self.assertEqual(stored.attachment.bucket_id, poll.bucket_id)
        self.assertEqual(stored.attachment.directory_id, poll.directory_id)
        feed = client_of(self.member).get(self.url("thread_feed", poll.pk)).json()
        self.assertEqual([row["text"] for row in feed["messages"]], ["First", "Second"])
        self.assertEqual(feed["poll"]["comment_count"], 2)

    def test_feeds_and_search_obey_community_access(self):
        poll = ChannelPoll.objects.get(pk=self.create(title="Harbour walk?").json()["poll"]["id"])
        self.reply(self.member, poll, "Bring the blue lantern")
        own = threads.page(self.member, self.guild.__class__.objects.filter(pk=self.guild.pk),
                           query="lantern")
        self.assertEqual([card["id"] for card in own["cards"]], [str(poll.pk)])
        self.assertEqual(threads.page(self.outsider, [self.other], query="lantern")["cards"], [])
        self.assertEqual(len(threads.page(self.member, [self.guild], query="harbour")["cards"]), 1)
        self.assertEqual(len(threads.page(self.member, [self.guild], query="decide")["cards"]), 1)
        send_json(client_of(self.member), self.url("poll_close", poll.pk))
        self.assertEqual(len(threads.page(self.member, [self.guild], mode="active")["cards"]), 1)
        self.assertEqual(len(threads.page(self.member, [self.guild], mode="closed")["cards"]), 1)
        self.assertEqual(client_of(self.outsider).get(
            self.url("thread_detail", poll.pk)).status_code, 403)
        page = client_of(self.member).get(self.url("channel_detail"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Harbour walk?")
        self.assertContains(page, "data-forum-create-dialog")

    def test_close_archive_delete_have_result_snapshots(self):
        poll = ChannelPoll.objects.get(pk=self.create().json()["poll"]["id"])
        choice = poll.choices.first()
        send_json(client_of(self.second), self.url("poll_vote", poll.pk),
                  {"choice": choice.pk})
        self.assertEqual(send_json(client_of(self.head),
                                   self.url("poll_remove", poll.pk)).status_code, 403)
        self.assertEqual(send_json(client_of(self.member),
                                   self.url("poll_close", poll.pk)).status_code, 200)
        self.assertEqual(send_json(client_of(self.admin),
                                   self.url("poll_archive", poll.pk)).status_code, 200)
        self.assertEqual(send_json(client_of(self.member),
                                   self.url("poll_remove", poll.pk)).status_code, 200)
        poll.refresh_from_db()
        self.assertIsNone(poll.bucket_id)
        self.assertIsNone(poll.directory_id)
        actions = list(ForumPollAudit.objects.filter(poll_id=poll.pk)
                       .order_by("created_at").values_list("action", flat=True))
        self.assertEqual(actions, ["create", "close", "archive", "delete"])
        deleted = ForumPollAudit.objects.get(poll_id=poll.pk, action="delete")
        self.assertEqual(poll_audit.read(deleted)["options"][0]["votes"], 1)
        audit_page = client_of(self.admin).get("/forum/settings/poll-audit/")
        self.assertContains(audit_page, "Where should we meet?")
        self.assertContains(audit_page, "archive")
        self.assertEqual(client_of(self.member).get(
            "/forum/settings/poll-audit/").status_code, 403)

    def test_encrypted_search_keeps_its_rate_limit(self):
        self.create(title="Harbour walk?")
        url = self.url("channel_detail")
        client = client_of(self.member)
        for _ in range(20):
            self.assertEqual(client.get(url, {"q": "harbour"}).status_code, 200)
        self.assertEqual(client.get(url, {"q": "harbour"}).status_code, 429)

    def test_deadline_closes_and_archived_thread_refuses_replies(self):
        poll = ChannelPoll.objects.get(pk=self.create().json()["poll"]["id"])
        ChannelPoll.objects.filter(pk=poll.pk).update(closes_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(client_of(self.member).get(
            self.url("thread_detail", poll.pk)).status_code, 200)
        poll.refresh_from_db()
        self.assertEqual(poll.status, "closed")
        self.assertTrue(ForumPollAudit.objects.filter(poll_id=poll.pk, action="close").exists())
        self.assertEqual(send_json(client_of(self.admin),
                                   self.url("poll_archive", poll.pk)).status_code, 200)
        self.assertEqual(self.reply(self.member, poll).status_code, 404)

    def test_cleanup_removes_inactive_whole_thread_and_keeps_audit(self):
        poll = ChannelPoll.objects.get(pk=self.create().json()["poll"]["id"])
        bucket_id = poll.bucket_id
        self.reply(self.member, poll, "A recent reply", upload())
        cutoff = timezone.now() - timedelta(days=10)
        self.assertEqual(cleanup.preview(cutoff, self.channel)["polls"], 0)
        ChannelPoll.objects.filter(pk=poll.pk).update(last_activity_at=cutoff - timedelta(days=1))
        self.assertEqual(cleanup.preview(cutoff, self.channel)["polls"], 0)
        send_json(client_of(self.member), self.url("poll_close", poll.pk))
        self.assertEqual(cleanup.preview(cutoff, self.channel)["messages"], 1)
        run = cleanup.claim(boundary=cutoff, retention_days=10,
                            triggered_by="manual", user=self.admin, channel=self.channel)
        cleanup.run_cleanup(run)
        self.assertFalse(ChannelPoll.objects.filter(pk=poll.pk).exists())
        self.assertFalse(ForumMessage.objects.filter(poll_id=poll.pk).exists())
        self.assertTrue(ForumPollAudit.objects.filter(poll_id=poll.pk, action="delete").exists())
        from toto.vault.models import Bucket
        self.assertFalse(Bucket.objects.filter(pk=bucket_id).exists())
