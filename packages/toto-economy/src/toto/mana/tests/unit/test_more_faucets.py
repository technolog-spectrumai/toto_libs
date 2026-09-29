"""The mana faucets as labels (2026-09-26): which faucet a claim key lands on,
that each pool's four are made once and repaired when one is missing, and that
the visible payout row is written once per claim however often it is asked for.
"""

from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from toto.assets.models import Faucet, FaucetPayout
from toto.mana import faucets, services
from toto.mana.tests.fixtures import economy, master

User = get_user_model()


class KeyTests(SimpleTestCase):
    def test_every_claim_key_lands_on_its_faucet(self):
        for key, kind in (("hourly:2026-09-29T10", "hourly"), ("signup", "signup"),
                          ("encrypt:12:2026-09-29", "encrypt-reward"),
                          ("manual:0a1b2c3d4e5f", "manual")):
            with self.subTest(key=key):
                self.assertEqual(faucets.kind_of_key(key), kind)

    def test_a_key_that_merely_contains_a_kind_is_not_that_kind(self):
        self.assertEqual(faucets.kind_of_key("signup-again"), "manual")
        self.assertEqual(faucets.kind_of_key("x:hourly:2026"), "manual")

    def test_the_slug_names_the_pool_and_the_kind(self):
        self.assertEqual(faucets.slug_for("security", "encrypt-reward"),
                         "mana-security-encrypt-reward")

    def test_every_kind_has_a_source_and_a_note(self):
        self.assertEqual({source for source, _note in faucets.KINDS.values()},
                         {"scheduled", "automatic", "manual"})
        self.assertTrue(all(note for _source, note in faucets.KINDS.values()))


@master
class FaucetRowTests(TestCase):
    def setUp(self):
        economy()
        self.pools = services.pools()
        self.pool = self.pools["compute"]

    def test_a_faucet_is_made_once_on_the_pools_asset(self):
        Faucet.objects.filter(slug="mana-compute-manual").delete()
        first = faucets.faucet_for(self.pool, "manual")
        again = faucets.faucet_for(self.pool, "manual")
        self.assertEqual(first.pk, again.pk)
        self.assertEqual((first.asset_id, first.source, first.active, first.name),
                         (self.pool.asset_id, "manual", True, "Mana compute — manual"))

    def test_ensure_repairs_only_what_is_missing_and_says_so(self):
        self.assertEqual(faucets.ensure_faucets(self.pools), 0)
        Faucet.objects.filter(slug__in=["mana-compute-hourly", "mana-storage-signup"]).delete()
        out = StringIO()
        self.assertEqual(faucets.ensure_faucets(self.pools, reporter=out), 2)
        self.assertIn("+ faucet mana-compute-hourly", out.getvalue())
        self.assertIn("+ faucet mana-storage-signup", out.getvalue())
        self.assertEqual(Faucet.objects.filter(slug__startswith="mana-").count(),
                         3 * len(faucets.KINDS))

    def test_ensure_without_a_reporter_is_silent(self):
        Faucet.objects.filter(slug="mana-security-manual").delete()
        self.assertEqual(faucets.ensure_faucets(self.pools), 1)

    def test_a_payout_is_written_once_per_claim(self):
        ada = User.objects.create_user("ada", password="pw")
        first = faucets.record_payout(self.pool, ada, key="manual:abc", amount_base_units=5,
                                      tx=None, reason="First")
        again = faucets.record_payout(self.pool, ada, key="manual:abc", amount_base_units=999,
                                      tx=None, reason="Second")
        self.assertEqual(first.pk, again.pk)
        again.refresh_from_db()
        self.assertEqual((again.amount_base_units, again.reason), (5, "First"))

    def test_a_long_reason_is_cut_to_the_column(self):
        ada = User.objects.create_user("ada", password="pw")
        payout = faucets.record_payout(self.pool, ada, key="manual:long", amount_base_units=1,
                                       tx=None, reason="x" * 500)
        self.assertEqual(len(payout.reason), 300)

    def test_the_payout_carries_the_faucets_source(self):
        ada = User.objects.create_user("ada", password="pw")
        payout = faucets.record_payout(self.pool, ada, key="hourly:2026-09-29T10",
                                       amount_base_units=4, tx=None)
        self.assertEqual((payout.faucet.slug, payout.source), ("mana-compute-hourly", "scheduled"))
        self.assertEqual(FaucetPayout.objects.filter(recipient=ada, faucet=payout.faucet).count(), 1)

    def test_a_real_grant_and_its_payout_name_the_same_transaction(self):
        from toto.mana.models import ManaGrant
        from toto.mana.tests.fixtures import spend

        ada = User.objects.create_user("ada", password="pw")
        spend(ada, "compute", "10")
        self.assertEqual(services.top_up(ada, self.pool, key="hourly:2026-09-29T10",
                                         cap=Decimal("4")), "paid")
        grant = ManaGrant.objects.get(user=ada, key="hourly:2026-09-29T10")
        self.assertEqual(grant.payout.transaction_id, grant.transaction_id)
        self.assertEqual(grant.payout.amount_base_units, grant.amount_base_units)
