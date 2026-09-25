"""Encryption at rest: sealing, the room key, and where the plaintext is not."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.forum import rooms, sealing, store
from toto.forum.models import ForumChannel, ForumMessage, ForumRoomKey

User = get_user_model()
VAULT = dict(FORUM_VAULT_PASSWORD="forum-test-vault-passphrase",
             FORUM_PASSWORD_KDF={"memory_cost": 8, "iterations": 1, "lanes": 1},
             FORUM_ALLOW_LOCAL_KEY_STORE=True)


class SealingTests(TestCase):
    key = b"k" * 32

    def test_round_trip(self):
        frame = sealing.seal_text(self.key, "hello", channel_id=1, message_id="m")
        self.assertEqual(sealing.open_text(self.key, frame, channel_id=1, message_id="m"), "hello")
        self.assertNotIn(b"hello", frame)

    def test_a_tampered_frame_is_refused(self):
        frame = bytearray(sealing.seal_text(self.key, "hello", channel_id=1, message_id="m"))
        frame[-1] ^= 1
        with self.assertRaises(sealing.SealBroken):
            sealing.open_text(self.key, bytes(frame), channel_id=1, message_id="m")

    def test_the_frame_is_bound_to_its_room_and_message(self):
        frame = sealing.seal_text(self.key, "hello", channel_id=1, message_id="m")
        for cid, mid in ((2, "m"), (1, "other")):
            with self.subTest(channel=cid, message=mid), self.assertRaises(sealing.SealBroken):
                sealing.open_text(self.key, frame, channel_id=cid, message_id=mid)

    def test_two_seals_of_one_text_differ(self):
        a = sealing.seal_text(self.key, "same", channel_id=1, message_id="m")
        b = sealing.seal_text(self.key, "same", channel_id=1, message_id="m")
        self.assertNotEqual(a, b)                  # a fresh nonce each time


@override_settings(**VAULT)
class RoomKeyTests(TestCase):
    def setUp(self):
        rooms.vault.clear_cache()
        cache.clear()
        self.user = User.objects.create_user("ada", password="x")

    def encrypted(self, name="Vault room", **extra):
        channel = ForumChannel(name=name, slug=name.lower().replace(" ", "-"),
                               created_by=self.user, is_encrypted=True, **extra)
        if channel.access == "password":
            rooms.set_password(channel, "correct horse")
        channel.save()
        return channel

    def test_a_persistent_room_key_is_stored_wrapped_and_opens(self):
        channel = self.encrypted()
        key = rooms.create_room_key(channel)
        row = ForumRoomKey.objects.get(channel=channel)
        self.assertNotIn(key, bytes(row.platform_wrapped))
        rooms.forget(channel)
        self.assertEqual(rooms.open_key(channel), key)

    def test_a_password_room_opens_with_the_password_alone(self):
        channel = self.encrypted(access="password")
        key = rooms.create_room_key(channel, password="correct horse")
        rooms.forget(channel)
        self.assertEqual(rooms.open_key_with_password(channel, "correct horse"), key)
        with self.assertRaises(rooms.RoomKeyUnavailable):
            rooms.open_key_with_password(channel, "wrong")

    def test_the_password_is_verified_and_never_stored(self):
        channel = self.encrypted(access="password")
        self.assertTrue(rooms.verify_password(channel, "correct horse"))
        self.assertFalse(rooms.verify_password(channel, "correct horsE"))
        stored = bytes(channel.password_verifier) + bytes(channel.password_salt)
        self.assertNotIn(b"correct horse", stored)

    def test_a_temporary_room_key_lives_only_in_the_cache(self):
        channel = self.encrypted(expires_at=timezone.now() + timedelta(hours=1))
        key = rooms.create_room_key(channel)
        self.assertFalse(ForumRoomKey.objects.filter(channel=channel).exists())
        self.assertEqual(rooms.open_key(channel), key)
        rooms.shred(channel)
        with self.assertRaises(rooms.RoomKeyUnavailable):
            rooms.open_key(channel)

    def test_without_the_platform_secret_the_room_is_unavailable_not_plaintext(self):
        channel = self.encrypted()
        rooms.create_room_key(channel)
        rooms.forget(channel)
        rooms.vault.clear_cache()
        with override_settings(FORUM_VAULT_PASSWORD=""):
            with self.assertRaises(rooms.RoomKeyUnavailable):
                rooms.open_key(channel)

    def test_a_stored_encrypted_message_carries_no_plaintext(self):
        channel = self.encrypted()
        key = rooms.create_room_key(channel)
        row = store.store_message(channel, msg_type="image_message", body="secret words",
                                  attachment=ContentFile(b"PNGDATA-secret"), attachment_name="a.png",
                                  key=key)
        row = ForumMessage.objects.get(pk=row.pk)
        self.assertEqual(row.body, "")
        self.assertNotIn(b"secret words", bytes(row.body_sealed))
        with row.attachment.open("rb") as fh:
            self.assertNotIn(b"PNGDATA-secret", fh.read())
        self.assertTrue(row.attachment_sealed)
        self.assertEqual(store.message_to_dict(row, key=key)["message"], "secret words")
        self.assertEqual(store.message_to_dict(row)["message"], "[encrypted]")

    def test_an_encrypted_room_stores_nothing_without_its_key(self):
        channel = self.encrypted()
        with self.assertRaises(rooms.RoomKeyUnavailable):
            store.store_message(channel, msg_type="chat_message", body="x")
        self.assertFalse(ForumMessage.objects.exists())

    def test_existing_rooms_are_ordinary_after_the_migration(self):
        room = ForumChannel.objects.create(name="Old", slug="old")
        self.assertEqual((room.access, room.is_encrypted, room.expires_at), ("open", False, None))
        row = store.store_message(room, msg_type="chat_message", body="plain")
        self.assertEqual(ForumMessage.objects.get(pk=row.pk).body, "plain")
