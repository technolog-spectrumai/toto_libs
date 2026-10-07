"""What is kept is kept sealed (stage 68, 2026-10-07).

Nothing a member wrote is readable in the database's rows or in the bytes
the vault keeps; what was sealed opens again; the key survives a restart (a
fresh process reads the wrapped row with the secret); another secret, or
none, fails closed.

    manage.py test toto.forum.tests.test_encryption
"""

from django.db import connection
from django.test import override_settings

from toto.forum import channels, keys, sealing
from toto.forum.models import (ChannelPoll, ForumChannel, ForumChannelKey, ForumMessage,
                               PollChoice)
from toto.forum.testing import PNG, ForumCase, client_of, restart, upload
from toto.vault.storage_backends import read_file_bytes

WORDS = ("the harbour at dawn", "Where do we sail?", "Gdansk", "by the old crane",
         "Kolobrzeg")


def _every_stored_value() -> bytes:
    """Every value of every forum table, as bytes, read past the ORM."""
    out = []
    with connection.cursor() as cursor:
        for table in connection.introspection.table_names(cursor):
            if not table.startswith("forum_"):
                continue
            cursor.execute(f'SELECT * FROM "{table}"')
            for row in cursor.fetchall():
                for value in row:
                    if isinstance(value, memoryview):
                        value = bytes(value)
                    out.append(value if isinstance(value, bytes) else str(value).encode())
    return b"\n".join(out)


class AtRestTests(ForumCase):
    def setUp(self):
        super().setUp()
        self.message = self.say(self.member, WORDS[0], image=upload()).json()["message"]
        self.poll = self.open_poll(self.member, title=WORDS[1],
                                   options=f"{WORDS[2]}: {WORDS[3]}\n{WORDS[4]}").json()["poll"]

    def test_nothing_readable_in_any_row(self):
        stored = _every_stored_value()
        for word in WORDS:
            self.assertNotIn(word.encode(), stored, word)
        self.assertNotIn(b"picture-of-the-harbour", stored)

    def test_there_is_no_plaintext_column(self):
        names = {field.name for field in ForumMessage._meta.get_fields()}
        self.assertNotIn("body", names)
        self.assertFalse({"title", "question_text", "slug"}
                         & {field.name for field in ChannelPoll._meta.get_fields()})
        self.assertFalse({"label", "text"}
                         & {field.name for field in PollChoice._meta.get_fields()})

    def test_the_image_bytes_in_the_vault_are_sealed(self):
        row = ForumMessage.objects.get(pk=self.message["id"])
        stored = read_file_bytes(row.attachment)
        self.assertNotIn(b"picture-of-the-harbour", stored)
        self.assertNotIn(b"PNG", stored)
        self.assertEqual(stored[:1], sealing.VERSION)
        self.assertTrue(row.attachment.is_encrypted)
        self.assertFalse(row.attachment.is_public)

    def test_a_round_trip_through_the_doors(self):
        answer = self.feed(self.second).json()
        self.assertEqual(answer["messages"][0]["text"], WORDS[0])
        poll = answer["polls"][0]
        self.assertEqual(poll["title"], WORDS[1])
        self.assertEqual([(c["label"], c["text"]) for c in poll["choices"]],
                         [(WORDS[2], WORDS[3]), (WORDS[4], "")])
        image = client_of(self.second).get(self.url("message_image", self.message["id"]))
        self.assertEqual(image.content, PNG)

    def test_a_sealed_body_does_not_open_on_another_message(self):
        """The seal binds a frame to its channel and its row."""
        key = keys.open_key(self.channel)
        row = ForumMessage.objects.get(pk=self.message["id"])
        self.assertEqual(sealing.open_text(key, row.body_sealed, channel_id=self.channel.pk,
                                           message_id=row.id), WORDS[0])
        other = self.say(self.member, "another").json()["message"]["id"]
        with self.assertRaises(sealing.SealBroken):
            sealing.open_text(key, row.body_sealed, channel_id=self.channel.pk,
                              message_id=other)
        with self.assertRaises(sealing.SealBroken):
            sealing.open_text(key, row.body_sealed, channel_id=self.channel.pk + 1,
                              message_id=row.id)

    def test_each_channel_has_its_own_key(self):
        other = channels.ensure_channel(self.other)
        self.assertNotEqual(keys.open_key(self.channel), keys.open_key(other))
        self.assertEqual(ForumChannelKey.objects.count(), 2)
        row = ForumMessage.objects.get(pk=self.message["id"])
        with self.assertRaises(sealing.SealBroken):
            sealing.open_text(keys.open_key(other), row.body_sealed,
                              channel_id=self.channel.pk, message_id=row.id)

    def test_the_key_is_stored_only_wrapped(self):
        key = keys.open_key(self.channel)
        self.assertEqual(len(key), 32)
        row = ForumChannelKey.objects.get(channel=self.channel)
        self.assertNotEqual(bytes(row.platform_wrapped), key)
        self.assertNotIn(key, _every_stored_value())


class KeyLifeTests(ForumCase):
    def setUp(self):
        super().setUp()
        self.message = self.say(self.member, WORDS[0]).json()["message"]

    def test_the_key_survives_a_restart(self):
        before = keys.open_key(self.channel)
        restart()                       # a fresh process: nothing in memory
        self.assertEqual(keys.open_key(self.channel), before)
        self.assertEqual(self.feed(self.member).json()["messages"][0]["text"], WORDS[0])

    def test_another_secret_fails_closed(self):
        restart()
        with override_settings(FORUM_VAULT_PASSWORD="another-secret-entirely"):
            with self.assertRaises(keys.ChannelKeyUnavailable):
                keys.open_key(self.channel)
            self.assertEqual(self.feed(self.member).status_code, 503)
            self.assertEqual(self.say(self.member, "lost").status_code, 503)
            self.assertEqual(client_of(self.member).get(self.url("channel_detail")).status_code,
                             503)
            restart()
        self.assertEqual(ForumMessage.objects.count(), 1)
        self.assertEqual(self.feed(self.member).json()["messages"][0]["text"], WORDS[0])

    def test_no_secret_fails_closed_and_stores_nothing(self):
        restart()
        with override_settings(FORUM_VAULT_PASSWORD=""):
            self.assertEqual(self.feed(self.member).status_code, 503)
            response = self.say(self.member, "in the clear?")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("in the clear?", response.content.decode())
            self.assertEqual(self.open_poll(self.member).status_code, 503)
            restart()
        self.assertEqual(ForumMessage.objects.count(), 1)
        self.assertEqual(ChannelPoll.objects.count(), 0)

    def test_no_channel_is_made_without_the_secret(self):
        restart()
        with override_settings(FORUM_VAULT_PASSWORD=""):
            # The strongbox exists already (the Guild's): a new channel still
            # needs the secret to wrap its key.
            self.outsider_person.communities.add(self.other)
            response = client_of(self.outsider).get(self.url("channel_detail",
                                                             community=self.other))
            self.assertEqual(response.status_code, 503)
            restart()
        self.assertFalse(ForumChannel.objects.filter(community=self.other).exists())

    def test_there_is_no_password_anywhere(self):
        names = {field.name for model in (ForumChannel, ForumChannelKey)
                 for field in model._meta.get_fields()}
        self.assertFalse([name for name in names if "password" in name or "kdf" in name])
        self.assertFalse(hasattr(keys, "set_password"))
        self.assertFalse(hasattr(keys, "open_key_with_password"))
