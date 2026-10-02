"""Whole paths, end to end — what the unit tests take one step at a time.

These go through management commands, the tax sweep, the real encryption
strategy and the charge ladder. Run by tests/user_tests.sh (from the zenobia
monorepo root), not by the quick loop.
"""

from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from toto.assets.models import LedgerTransaction
from toto.mana import services
from toto.mana.models import ManaGrant, ManaPool
from toto.mana.tests.fixtures import MASTER, economy, held, platform, spend
from toto.tax.tests.factories import GB, make_vault_file

User = get_user_model()


def quietly(*command):
    call_command(*command, stdout=StringIO(), stderr=StringIO())


@override_settings(**MASTER)
class IngressFlowTests(TestCase):
    def test_an_unrelated_ingress_command_produces_the_pools(self):
        """Obligatory: it rides IngressCommand.bootstrap, not one command."""
        platform()
        User.objects.create_superuser("root", "r@x.invalid", "x")
        quietly("ingress_quota")
        self.assertEqual(ManaPool.objects.count(), 3)

    def test_members_who_predate_the_pools_are_filled_once(self):
        platform()
        old = User.objects.create_user("old", password="pw")     # no pools yet
        economy()
        self.assertEqual(held(old, "compute"), Decimal("0"))
        quietly("ingress_mana")
        self.assertEqual(held(old, "compute"), Decimal("100"))
        before = LedgerTransaction.objects.count()
        quietly("ingress_mana")
        self.assertEqual(LedgerTransaction.objects.count(), before)


@override_settings(**MASTER)
class EncryptFlowTests(TestCase):
    def test_a_real_encryption_earns_through_the_whole_path(self):
        """Strategy, signal, receiver, ledger — nothing mocked."""
        from toto.gervazy.models import UserStrongbox

        economy()
        ada = User.objects.create_user("ada", password="pw")
        UserStrongbox.objects.create(owner=ada, name="sb")
        spend(ada, "security", "50")
        f = make_vault_file(ada, 1024)
        f.encrypt(password="correct horse battery staple")
        f.refresh_from_db()
        self.assertTrue(f.is_encrypted)
        self.assertEqual(held(ada, "security"), Decimal("60"))


@override_settings(**MASTER)
class MembersDayTests(TestCase):
    """One member, one day: act, hold plaintext, get refused, encrypt, refill."""

    def test_a_day_of_mana(self):
        from toto.gervazy.models import UserStrongbox
        from toto.quota.charge import InsufficientFunds, charge, check_funds, price_for
        from toto.tax import services as tax
        from toto.tax.models import TaxArrearsCase, TaxRule

        economy()
        quietly("ingress_mana")
        ada = User.objects.create_user("ada", password="pw")
        UserStrongbox.objects.create(owner=ada, name="sb")

        # Uploading draws on storage through the ordinary charge ladder.
        vault = price_for(ada, "vault")
        charge(ada, vault, "storage.request", 4)
        self.assertEqual(held(ada, "storage"), Decimal("98"))

        # Holding 6 GB of plaintext drains more than a pool holds: clamped.
        big = make_vault_file(ada, 6 * GB)
        tax.levy_rule(TaxRule.objects.get(metric_code="security.plain_gb_day"))
        self.assertEqual(held(ada, "security"), Decimal("0"))
        self.assertFalse(TaxArrearsCase.objects.exists())

        # An empty security pool refuses a scan in words — and nothing else.
        scan = price_for(ada, "antivirus")
        if scan is not None:
            with self.assertRaises(InsufficientFunds) as caught:
                check_funds(ada, scan, "antivirus.scan", 1)
            self.assertIn("security mana", str(caught.exception))
        check_funds(ada, price_for(ada, "workflows"), "workflows.run", 1)

        # Encrypting the plaintext earns security mana back.
        big.encrypt(password="correct horse battery staple")
        self.assertEqual(held(ada, "security"), Decimal("10"))

        # And the hourly refill tops every pool up toward its cap.
        report = services.regenerate_hour()
        self.assertGreaterEqual(report.paid, 2)
        self.assertEqual(held(ada, "security"), Decimal("14"))
        self.assertEqual(held(ada, "storage"), Decimal("100"))
        self.assertTrue(ManaGrant.objects.filter(user=ada, key__startswith="hourly:").exists())
