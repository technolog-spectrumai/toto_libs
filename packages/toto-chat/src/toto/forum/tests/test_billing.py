"""Forum charges: once per message, never for a refused or failed send."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.test import TestCase, override_settings

from toto.forum import billing, creation, rooms, store
from toto.forum.models import ForumMessage, ForumUsageEvent

User = get_user_model()
FAST = dict(FORUM_PASSWORD_KDF={"memory_cost": 8, "iterations": 1, "lanes": 1},
            FORUM_VAULT_PASSWORD="forum-test-vault-passphrase", FORUM_ALLOW_LOCAL_KEY_STORE=True)


@override_settings(**FAST)
class BillingTests(TestCase):
    def setUp(self):
        from toto.people.models import Person

        cache.clear()
        self.user = User.objects.create_user("ada", password="x")
        Person.objects.create(user=self.user, display_name="Ada")
        self.room = creation.create_room(self.user, name="Talk")

    def send(self, room=None, body="hi"):
        room = room or self.room
        key = rooms.open_key(room) if room.is_encrypted else None
        with transaction.atomic():
            row = store.store_message(room, msg_type="chat_message", body=body,
                                      sender=self.user, key=key)
            billing.settle_message(self.user, row)
        return row

    def test_a_send_records_one_security_event_and_a_retry_adds_nothing(self):
        row = self.send()
        self.assertEqual(list(ForumUsageEvent.objects.values_list("metric_code", flat=True)),
                         ["forum.message"])
        with mock.patch("toto.forum.billing.charge") as charged:
            billing.settle_message(self.user, row)
        charged.assert_not_called()
        self.assertEqual(ForumUsageEvent.objects.count(), 1)

    def test_an_encrypted_send_bills_the_seal_not_the_plaintext_rate(self):
        room = creation.create_room(self.user, name="Sealed", encrypted=True)
        self.assertTrue(ForumUsageEvent.objects.filter(metric_code="forum.room_key").exists())
        self.send(room)
        self.assertTrue(ForumUsageEvent.objects.filter(metric_code="forum.encrypt").exists())
        self.assertFalse(ForumUsageEvent.objects.filter(metric_code="forum.message",
                                                        source_id__in=ForumMessage.objects.filter(
                                                            channel=room).values("id")).exists())

    def test_insufficient_mana_refuses_and_stores_nothing(self):
        from toto.quota.charge import InsufficientFunds

        with mock.patch("toto.forum.billing.check_funds",
                        side_effect=InsufficientFunds("BLUE", 1, 0)):
            with self.assertRaises(InsufficientFunds):
                billing.check_affordable(self.user, self.room)
        self.assertFalse(ForumMessage.objects.exists())
        self.assertFalse(ForumUsageEvent.objects.exists())

    def test_a_failed_charge_rolls_the_message_back(self):
        with mock.patch("toto.forum.billing.charge", side_effect=RuntimeError("ledger down")):
            with self.assertRaises(RuntimeError):
                self.send()
        self.assertFalse(ForumMessage.objects.exists())
        self.assertFalse(ForumUsageEvent.objects.exists())

    def test_a_failed_store_is_never_charged(self):
        with mock.patch("toto.forum.billing.charge") as charged:
            with mock.patch.object(ForumMessage, "save", side_effect=RuntimeError("disk")):
                with self.assertRaises(RuntimeError):
                    self.send()
        charged.assert_not_called()
        self.assertFalse(ForumUsageEvent.objects.exists())

    def test_the_metrics_are_coloured(self):
        from toto.mana.colours import COLOUR_OF

        self.assertEqual(COLOUR_OF["forum.message"], "security")
        self.assertEqual(COLOUR_OF["forum.encrypt"], "compute")
        self.assertEqual(COLOUR_OF["forum.room_key"], "compute")


@override_settings(**FAST)
class PricedChargeTests(TestCase):
    """With a price, the ledger's own charge_user is reached — with ITS
    signature (autospec). Without a price nothing is charged at all, which is
    how a stray `source_label` keyword 500'd /forum/create/ on every priced
    host while every test here passed."""

    def setUp(self):
        from toto.people.models import Person

        cache.clear()
        self.user = User.objects.create_user("ada", password="x")
        Person.objects.create(user=self.user, display_name="Ada")

    def test_priced_room_creation_and_messages_reach_charge_user_cleanly(self):
        with mock.patch("toto.forum.billing.price_for", return_value=object()), \
                mock.patch("toto.forum.billing.check_funds"), \
                mock.patch("toto.tariffs.charge.charge_user", autospec=True) as charge_user:
            for encrypted in (False, True):
                room = creation.create_room(self.user, name=f"Priced {encrypted}",
                                            encrypted=encrypted)
                key = rooms.open_key(room) if room.is_encrypted else None
                with transaction.atomic():
                    row = store.store_message(room, msg_type="chat_message", body="hi",
                                              sender=self.user, key=key)
                    billing.settle_message(self.user, row)
        self.assertGreaterEqual(charge_user.call_count, 3)
        for call in charge_user.call_args_list:
            self.assertNotIn("source_label", call.kwargs)
            self.assertIn("description", call.kwargs)
