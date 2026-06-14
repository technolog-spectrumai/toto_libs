"""Tests for the relay "Discord" model: encrypted-at-rest history, TTL/purge, and
end-to-end (server-opaque) pins."""
import base64
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.telegraph import vault
from toto.telegraph.models import (
    TelegraphChannel,
    TelegraphMember,
    TelegraphMessage,
)

User = get_user_model()
PW = "test-telegraph-pw"


@override_settings(TELEGRAPH_VAULT_PASSWORD=PW)
class VaultHistoryTests(TestCase):
    def setUp(self):
        vault.clear_cache()
        call_command("telegraph_init_vault", verbosity=0)
        vault.clear_cache()
        self.channel = TelegraphChannel.objects.create(
            name="hist", slug="hist", message_ttl_seconds=86400
        )

    def test_store_is_encrypted_at_rest_and_decrypts(self):
        row = vault.store_message(
            self.channel, msg_type="chat_message",
            payload={"message": "launch is friday"}, sender_name="alice",
        )
        # The stored ciphertext must not contain the plaintext.
        self.assertNotIn(b"launch is friday", bytes(row.ciphertext))
        self.assertEqual(vault.decrypt_message(row), {"message": "launch is friday"})
        self.assertIsNotNone(row.expires_at)

    def test_per_channel_key_isolation(self):
        other = TelegraphChannel.objects.create(name="o", slug="o")
        r1 = vault.store_message(self.channel, msg_type="chat_message", payload={"message": "a"})
        vault.store_message(other, msg_type="chat_message", payload={"message": "b"})
        self.channel.refresh_from_db(); other.refresh_from_db()
        self.assertNotEqual(self.channel.dek_id, other.dek_id)  # one DEK per channel
        self.assertEqual(vault.decrypt_message(r1), {"message": "a"})

    def test_history_oldest_first_with_metadata(self):
        vault.store_message(self.channel, msg_type="chat_message", payload={"message": "first"}, sender_name="a")
        vault.store_message(self.channel, msg_type="chat_message", payload={"message": "second"}, sender_name="b")
        hist = vault.history(self.channel)
        self.assertEqual([h["message"] for h in hist], ["first", "second"])
        self.assertEqual(hist[0]["type"], "chat_message")
        self.assertEqual(hist[0]["user"], "a")
        self.assertTrue(hist[0]["id"])
        self.assertTrue(hist[0]["history"])

    def test_expired_excluded_then_purged(self):
        row = vault.store_message(self.channel, msg_type="chat_message", payload={"message": "old"})
        TelegraphMessage.objects.filter(pk=row.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(vault.history(self.channel), [])      # excluded from replay
        self.assertEqual(vault.purge_expired(self.channel), 1)  # and deleted
        self.assertFalse(TelegraphMessage.objects.filter(pk=row.pk).exists())

    def test_unavailable_when_strongbox_missing(self):
        from toto.gervazy.models import UserStrongbox

        # No telegraph-system strongbox in the lookup → vault unavailable (rename rather
        # than delete: the VMK/DEK FKs are PROTECT).
        UserStrongbox.objects.filter(name=vault.SYSTEM_STRONGBOX_NAME).update(
            name="archived-telegraph-box"
        )
        vault.clear_cache()
        with self.assertRaises(vault.VaultUnavailable):
            vault.open_session()

    # ── secure-on-send (end-to-end; server stores opaque ciphertext) ──────────
    def test_e2e_message_stored_opaque(self):
        row = vault.store_e2e_message(
            self.channel, pin_key_id="k1", iv=b"x" * 12, ciphertext=b"opaque-e2e-bytes",
            sender_name="alice",
        )
        self.assertEqual(row.encryption, "e2e")
        self.assertEqual(row.pin_key_id, "k1")
        self.assertEqual(bytes(row.ciphertext), b"opaque-e2e-bytes")
        self.assertIsNotNone(row.expires_at)  # follows the channel TTL

    def test_history_replays_e2e_ciphertext_raw(self):
        import base64

        vault.store_e2e_message(
            self.channel, pin_key_id="k1", iv=b"i" * 12, ciphertext=b"cipher",
            sender_name="alice",
        )
        hist = vault.history(self.channel)
        self.assertEqual(len(hist), 1)
        entry = hist[0]
        self.assertEqual(entry["type"], "secure_message")
        self.assertEqual(entry["pin_key_id"], "k1")
        self.assertEqual(base64.b64decode(entry["ciphertext"]), b"cipher")
        self.assertEqual(base64.b64decode(entry["iv"]), b"i" * 12)
        self.assertNotIn("message", entry)  # server never had the plaintext

    def test_e2e_history_works_without_vault_password(self):
        from toto.gervazy.models import UserStrongbox

        vault.store_e2e_message(
            self.channel, pin_key_id="k1", iv=b"i" * 12, ciphertext=b"c", sender_name="a"
        )
        # Even with no usable vault, e2e history is replayable (server holds no key for it).
        UserStrongbox.objects.filter(name=vault.SYSTEM_STRONGBOX_NAME).update(
            name="archived"
        )
        vault.clear_cache()
        hist = vault.history(self.channel)
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["type"], "secure_message")

    def test_e2e_message_purged_after_ttl(self):
        row = vault.store_e2e_message(
            self.channel, pin_key_id="k1", iv=b"i" * 12, ciphertext=b"c", sender_name="a"
        )
        TelegraphMessage.objects.filter(pk=row.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(vault.purge_expired(self.channel), 1)

