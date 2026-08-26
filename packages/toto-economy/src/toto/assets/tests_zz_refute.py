"""Refutation probe for: '_reconcile is unreachable once the member is removed'."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.assets.models import (Asset, Faucet, FaucetMember, FaucetPayout,
                                FaucetPayoutStatus, LedgerTransaction)
from toto.assets.services import faucets
from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform

User = get_user_model()


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
class Refute(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        self.mana = Asset.objects.get(unit_name="MANA")
        self.faucet = Faucet.objects.create(name="Stipends", asset=self.mana,
                                            active=True)
        self.ada = User.objects.create_user("ada", password="pw")
        self.member = FaucetMember.objects.create(
            faucet=self.faucet, user=self.ada, amount_per_hour=Decimal("2"))

    def account(self, user):
        from toto.assets.prepaid import get_or_create_prepaid_account
        acct, _ = get_or_create_prepaid_account(user)
        return acct

    def crash_state(self, label):
        """Exactly the CrashRecoveryTests state: claim + money, no record."""
        from toto.assets.services.assets import distribute_asset
        FaucetPayout.objects.create(
            member=self.member, period_label=label,
            amount_base_units=2 * 10 ** self.mana.decimals,
            status=FaucetPayoutStatus.PENDING)
        return distribute_asset(
            asset=self.mana, recipient_account=self.account(self.ada),
            amount=Decimal("2"), reference=faucets.reference_for(self.member, label))

    def faucet_txs(self):
        return LedgerTransaction.objects.filter(reference__startswith="faucet-")

    def row(self, label):
        return FaucetPayout.objects.get(member=self.member, period_label=label)

    # ------------------------------------------------------------------ #
    def test_the_row_is_equally_stuck_with_the_member_STILL_ACTIVE(self):
        """The finding blames 'press Remove'. Nothing is removed here."""
        now = timezone.now()
        label = faucets.period_label(now)
        self.crash_state(label)

        # The beat's next firings. It reads the CLOCK, so each is a new hour.
        for h in (1, 2, 3):
            faucets.run_hour(at=now + timezone.timedelta(hours=h))

        r = self.row(label)
        self.assertTrue(self.member.active)
        self.assertTrue(Faucet.objects.get(pk=self.faucet.pk).active)
        self.assertEqual(r.status, FaucetPayoutStatus.PENDING)   # still stuck
        self.assertIsNone(r.transaction_id)
        self.assertEqual(r.detail, "")

    def test_removal_changes_nothing_about_that_row(self):
        """Same crash, then Remove. Identical end state -> removal is not the cause."""
        now = timezone.now()
        label = faucets.period_label(now)
        self.crash_state(label)
        self.member.active = False
        self.member.save(update_fields=["active"])

        for h in (1, 2, 3):
            faucets.run_hour(at=now + timezone.timedelta(hours=h))

        r = self.row(label)
        self.assertEqual(r.status, FaucetPayoutStatus.PENDING)
        self.assertIsNone(r.transaction_id)

    def test_no_money_is_wrong_in_either_case(self):
        """The recipient holds exactly one hour's pay; nothing double-pays."""
        now = timezone.now()
        label = faucets.period_label(now)
        self.crash_state(label)
        self.member.active = False
        self.member.save(update_fields=["active"])

        for h in (1, 2, 3):
            faucets.run_hour(at=now + timezone.timedelta(hours=h))
        faucets.run_hour(at=now)          # a manual re-run of the crashed hour

        self.assertEqual(self.faucet_txs().count(), 1)
        self.assertEqual(FaucetPayout.objects.filter(period_label=label).count(), 1)

    def test_the_row_heals_the_moment_the_member_is_put_back_and_the_hour_rerun(self):
        """Re-adding (update_or_create sets active=True, same pk -> same
        reference) and re-running that hour reconciles it. Not permanent."""
        now = timezone.now()
        label = faucets.period_label(now)
        tx = self.crash_state(label)
        self.member.active = False
        self.member.save(update_fields=["active"])

        FaucetMember.objects.update_or_create(
            faucet=self.faucet, user=self.ada,
            defaults={"amount_per_hour": Decimal("2"), "active": True})
        faucets.run_hour(at=now)

        r = self.row(label)
        self.assertEqual(r.status, FaucetPayoutStatus.PAID)
        self.assertEqual(r.transaction_id, tx.pk)
        self.assertEqual(self.faucet_txs().count(), 1)
