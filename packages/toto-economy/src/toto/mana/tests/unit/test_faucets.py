"""Every mana increase is a faucet payout (2026-09-26): the hourly refill, the
opening fill, the encrypt reward and a grant by hand — each visible, each once,
and nothing else puts mana on an account."""

from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.assets.models import Faucet, FaucetPayout, FaucetRun
from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.services.faucets import period_label
from toto.core.models import Platform
from toto.mana import faucets, services
from toto.mana.models import ManaGrant

from .test_regen import MASTER

User = get_user_model()


@override_settings(**MASTER)
class FaucetTestCase(TestCase):
    HOUR = timezone.now().replace(minute=30, second=0, microsecond=0)

    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test", defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        self.pools = services.pools()
        self.ada = User.objects.create_user("ada", password="pw")     # arrives full
        self.staff = User.objects.create_user("stan", password="pw", is_staff=True)

    def payouts(self, kind, role="security", user=None):
        rows = FaucetPayout.objects.filter(faucet__slug=faucets.slug_for(role, kind))
        return list(rows.filter(recipient=user or self.ada))


class BootstrapTests(FaucetTestCase):
    def test_every_pool_has_its_four_faucets(self):
        for role in self.pools:
            for kind, (source, _note) in faucets.KINDS.items():
                faucet = Faucet.objects.get(slug=faucets.slug_for(role, kind))
                self.assertEqual((faucet.source, faucet.asset_id, faucet.active),
                                 (source, self.pools[role].asset_id, True))
        self.assertEqual(Faucet.objects.filter(slug__startswith="mana-").count(), 3 * len(faucets.KINDS))

    def test_the_members_sweep_never_pays_the_mana_faucets(self):
        from toto.assets.services.faucets import due_members

        self.assertFalse(due_members().filter(faucet__slug__startswith="mana-").exists())


class PayoutTests(FaucetTestCase):
    def test_the_opening_fill_is_a_signup_payout(self):
        rows = self.payouts("signup")
        self.assertEqual(len(rows), 1)
        payout = rows[0]
        grant = ManaGrant.objects.get(user=self.ada, role="security", key="signup")
        self.assertEqual((payout.recipient, payout.source, payout.status, payout.period_label),
                         (self.ada, "automatic", "paid", "signup"))
        self.assertEqual(payout.transaction, grant.transaction)
        self.assertEqual(grant.payout, payout)

    def test_the_hourly_refill_is_one_payout_and_one_run_per_pool_however_often_it_runs(self):
        from ..fixtures import spend

        spend(self.ada, "compute", 10)
        first = services.regenerate_hour(at=self.HOUR)
        again = services.regenerate_hour(at=self.HOUR)
        self.assertEqual((first.paid, again.paid), (1, 0))
        label = period_label(self.HOUR)
        rows = FaucetPayout.objects.filter(recipient=self.ada, period_label=label)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().faucet.slug, "mana-compute-hourly")
        self.assertEqual(rows.get().source, "scheduled")
        runs = FaucetRun.objects.filter(faucet__slug="mana-compute-hourly", period_label=label)
        self.assertEqual(runs.count(), 2)                          # one per execution
        self.assertEqual([r.paid for r in runs.order_by("pk")], [1, 0])

    def test_the_encrypt_reward_is_an_automatic_payout(self):
        from toto.vault.models import Bucket, VaultFile

        from ..fixtures import spend

        spend(self.ada, "security", 50)
        bucket = Bucket.objects.create(owner=self.ada, name="B", slug="b-ada", storage_backend="local")
        page = VaultFile.objects.create(owner=self.ada, title="x.txt", file_type="text", bucket=bucket,
                                        file=SimpleUploadedFile("x.txt", b"x"))
        self.assertIsNotNone(services.reward_encrypt(page))
        self.assertIsNone(services.reward_encrypt(page))            # once per file per day
        rows = self.payouts("encrypt-reward")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source, "automatic")

    def test_the_history_names_the_faucet(self):
        row = next(r for r in services.history(self.ada, "security") if r["kind"] == "signup")
        self.assertEqual(row["faucet"], "Mana security — signup")


class ManualGrantTests(FaucetTestCase):
    def setUp(self):
        super().setUp()
        from ..fixtures import spend

        spend(self.ada, "security", 40)
        self.faucet = Faucet.objects.get(slug="mana-security-manual")
        self.url = reverse("assets:faucet_grant", args=[self.faucet.pk])

    def test_a_grant_needs_a_reason_and_pays_toward_the_maximum(self):
        with self.assertRaises(services.GrantRefused):
            services.grant_manual(self.ada, self.pools["security"], 10, reason="  ", granted_by=self.staff)
        outcome = services.grant_manual(self.ada, self.pools["security"], 10, reason="Contest prize",
                                        granted_by=self.staff)
        self.assertEqual(outcome, "paid")
        payout = self.payouts("manual")[0]
        self.assertEqual((payout.source, payout.reason, payout.granted_by, payout.amount_base_units),
                         ("manual", "Contest prize", self.staff, 10 * 10 ** 9))
        # Toward the maximum: 100 - 60 held = 40 room; a grant of 500 pays 30 more, no further.
        services.grant_manual(self.ada, self.pools["security"], 500, reason="Big", granted_by=self.staff)
        from ..fixtures import held

        self.assertEqual(held(self.ada, "security"), Decimal("100"))

    def test_the_door_is_for_staff_and_the_reason_is_required(self):
        member = User.objects.create_user("bob", password="pw")
        self.client.force_login(member)
        self.assertEqual(self.client.post(self.url, {"user": self.ada.pk, "amount": "5", "reason": "x"}).status_code, 403)
        self.client.force_login(self.staff)
        self.client.post(self.url, {"user": self.ada.pk, "amount": "5", "reason": ""})
        self.assertEqual(self.payouts("manual"), [])
        self.client.post(self.url, {"user": self.ada.pk, "amount": "5", "reason": "Helping out"})
        self.assertEqual(len(self.payouts("manual")), 1)
        page = self.client.get(reverse("assets:faucet_detail", args=[self.faucet.pk]))
        self.assertContains(page, "Helping out")
        self.assertContains(page, 'data-testid="faucet-grant-form"')

    def test_distribute_refuses_a_mana_asset(self):
        from toto.assets.prepaid import get_or_create_prepaid_account

        account, _ = get_or_create_prepaid_account(self.ada)
        asset = self.pools["security"].asset
        self.client.force_login(self.staff)
        response = self.client.post(reverse("assets:asset_distribute", args=[asset.code]),
                                    {"amount": "5", "recipient_account": account.pk}, follow=True)
        self.assertContains(response, "mana pool")
        self.assertFalse(ManaGrant.objects.filter(user=self.ada, key__startswith="dist").exists())
        from toto.assets.models import LedgerTransaction

        self.assertFalse(LedgerTransaction.objects.filter(reference__startswith="dist-").exists())
        self.assertContains(self.client.get(asset.get_absolute_url()),
                            'data-testid="mana-no-distribute"')

    def test_the_admin_cannot_type_a_balance(self):
        from toto.assets.admin import AssetHoldingAdmin

        self.assertIn("balance_base_units", AssetHoldingAdmin.readonly_fields)
