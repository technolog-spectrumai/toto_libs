"""Encrypting a vault file earns security mana — capped, and never twice."""

from decimal import Decimal
from itertools import count
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from toto.assets.prepaid import get_or_create_prepaid_account
from toto.assets.services.assets import transfer_asset
from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform
from toto.mana import services
from toto.mana.models import ManaGrant
from toto.tax.tests.factories import make_vault_file

MASTER = dict(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
_ref = count()


@override_settings(**MASTER)
class RewardTestCase(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        self.pool = services.pools()["security"]
        self.ada = get_user_model().objects.create_user("ada", password="pw")

    def held(self):
        return Decimal(services.balance_base_units(self.ada, self.pool)) / 10 ** 9

    def spend(self, amount):
        account, _ = get_or_create_prepaid_account(self.ada)
        transfer_asset(asset=self.pool.asset, sender_account=account,
                       receiver_account=self.pool.asset.reserve_account,
                       amount=Decimal(amount), reference=f"test-spend-{next(_ref)}")

    def file(self):
        return make_vault_file(self.ada, 1024)


class RewardTests(RewardTestCase):
    def test_encrypting_earns_ten(self):
        self.spend("50")
        self.assertIsNotNone(services.reward_encrypt(self.file()))
        self.assertEqual(self.held(), Decimal("60"))

    def test_the_same_file_the_same_day_earns_once(self):
        self.spend("50")
        f = self.file()
        services.reward_encrypt(f)
        self.assertIsNone(services.reward_encrypt(f))
        self.assertEqual(self.held(), Decimal("60"))

    def test_a_day_earns_at_most_thirty(self):
        self.spend("80")
        for _ in range(4):
            services.reward_encrypt(self.file())
        self.assertEqual(self.held(), Decimal("50"))

    def test_never_past_the_cap(self):
        self.spend("3")
        services.reward_encrypt(self.file())
        self.assertEqual(self.held(), Decimal("100"))

    def test_a_full_pool_earns_nothing_and_says_why(self):
        f = self.file()
        self.assertIsNone(services.reward_encrypt(f))
        grant = ManaGrant.objects.get(key__startswith=f"encrypt:{f.pk}:")
        self.assertEqual(grant.detail, "pool full")

    def test_the_reward_is_an_ordinary_ledger_transfer(self):
        from toto.assets.models import LedgerTransaction

        self.spend("50")
        f = self.file()
        tx = services.reward_encrypt(f)
        self.assertTrue(tx.reference.startswith(f"mana:reward:encrypt:{f.pk}:"))
        self.assertTrue(LedgerTransaction.objects.filter(pk=tx.pk, posted=True).exists())


class SignalTests(RewardTestCase):
    def test_the_signal_reaches_the_reward(self):
        from toto.vault.models import VaultFile
        from toto.vault.signals import file_encrypted

        self.spend("50")
        file_encrypted.send(sender=VaultFile, file=self.file())
        self.assertEqual(self.held(), Decimal("60"))

    def test_a_failing_reward_never_reaches_the_person_encrypting(self):
        from toto.vault.models import VaultFile
        from toto.vault.signals import file_encrypted

        with mock.patch("toto.mana.services.reward_encrypt",
                        side_effect=RuntimeError("boom")):
            file_encrypted.send(sender=VaultFile, file=self.file())   # no raise
