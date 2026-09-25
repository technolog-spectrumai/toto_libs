"""Temporary rooms expire whole; encrypted rooms keep retention and skip search."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.forum import creation, expiry, permissions, rooms, store
from toto.forum.models import ForumChannel, ForumCleanupRun, ForumMessage, TriggeredBy
from toto.forum.search import encrypted_rooms_skipped, search_messages

User = get_user_model()
FAST = dict(FORUM_PASSWORD_KDF={"memory_cost": 8, "iterations": 1, "lanes": 1},
            FORUM_VAULT_PASSWORD="forum-test-vault-passphrase", FORUM_ALLOW_LOCAL_KEY_STORE=True)


@override_settings(**FAST)
class ExpiryTests(TestCase):
    def setUp(self):
        from toto.people.models import Person

        cache.clear()
        self.user = User.objects.create_user("ada", password="x")
        Person.objects.create(user=self.user, display_name="Ada")

    def test_an_expired_room_refuses_reads_before_the_sweep(self):
        room = creation.create_room(self.user, name="Brief", expires_in="1h")
        self.assertTrue(permissions.can_read(self.user, room))
        ForumChannel.objects.filter(pk=room.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        room.refresh_from_db()
        self.assertFalse(permissions.can_read(self.user, room))
        self.assertFalse(permissions.can_send(self.user, room.slug))

    def test_expiry_deletes_the_room_its_messages_and_its_key(self):
        room = creation.create_room(self.user, name="Brief", expires_in="1h", encrypted=True)
        key = rooms.open_key(room)
        store.store_message(room, msg_type="chat_message", body="gone soon", key=key)
        ForumChannel.objects.filter(pk=room.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        results = expiry.expire_due()
        self.assertEqual([r["ok"] for r in results], [True])
        self.assertFalse(ForumChannel.objects.filter(pk=room.pk).exists())
        self.assertFalse(ForumMessage.objects.exists())
        run = ForumCleanupRun.objects.get(triggered_by=TriggeredBy.EXPIRY)
        self.assertEqual(run.channel_name, "Brief")
        self.assertIsNone(cache.get(f"forum:roomkey:{room.pk}"))

    def test_a_room_not_yet_expired_is_left_alone(self):
        room = creation.create_room(self.user, name="Later", expires_in="24h")
        self.assertEqual(expiry.expire_due(), [])
        self.assertTrue(ForumChannel.objects.filter(pk=room.pk).exists())

    def test_the_task_is_scheduled(self):
        from toto.schedules import beat_schedule

        schedule = beat_schedule(forum_cleanup=True)
        self.assertEqual(schedule["forum-expire"]["task"], "toto.forum.tasks.forum_expire")


@override_settings(**FAST)
class EncryptedRetentionAndSearchTests(TestCase):
    def setUp(self):
        from toto.people.models import Person

        cache.clear()
        self.user = User.objects.create_user("ada", password="x")
        Person.objects.create(user=self.user, display_name="Ada")
        self.plain = creation.create_room(self.user, name="Plain")
        self.sealed = creation.create_room(self.user, name="Sealed", encrypted=True)
        key = rooms.open_key(self.sealed)
        store.store_message(self.plain, msg_type="chat_message", body="apples here")
        store.store_message(self.sealed, msg_type="chat_message", body="apples hidden", key=key)

    def test_search_never_returns_a_sealed_row(self):
        results = list(search_messages(self.user, "apples"))
        self.assertEqual([r.channel_id for r in results], [self.plain.pk])
        self.assertEqual(encrypted_rooms_skipped(self.user), 1)

    def test_retention_sweeps_an_encrypted_room_and_keeps_its_key(self):
        from toto.forum import cleanup
        from toto.forum.models import ForumRetentionPolicy

        ForumMessage.objects.update(created_at=timezone.now() - timedelta(days=400))
        policy = ForumRetentionPolicy.objects.create(channel=self.sealed, enabled=True, retention_days=30)
        run = cleanup.trigger(triggered_by="manual", channel=self.sealed, policy=policy)
        cleanup.run_cleanup(run)
        self.assertFalse(ForumMessage.objects.filter(channel=self.sealed).exists())
        self.assertTrue(ForumMessage.objects.filter(channel=self.plain).exists())
        self.assertIsNotNone(rooms.open_key(self.sealed))
