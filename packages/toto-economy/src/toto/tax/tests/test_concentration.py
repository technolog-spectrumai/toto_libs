"""The anti-concentration term: k × share², added to whatever a levy measured.

A fixed supply plus a stipend loop makes wealth concentration a real failure
mode — whoever accumulates can outbid everyone for scarce capacity forever, and
no rate limit stops that. This is the regulator, and these are the properties
that make it one rather than just a bigger bill.

These tests used to ride the head tax, which is gone. They ride ``storage.gb_day``
now, and the end-to-end ones patch its ``sample()`` rather than creating files:
the term is generic and the subject of the test is the term, so a test that also
had to be true about how vault counts bytes would be testing two things and
failing for two reasons.
"""

from decimal import Decimal

from toto.assets.testing import LedgerTestCase as TestCase

from .. import services
from ..models import TaxRule
from .factories import make_gas_asset, make_user


METRIC = "storage.gb_day"
GB = 2 ** 30


def price_storage(amount="1"):
    from toto.quota.metrics import registry
    from toto.tariffs.rate_card import upsert_price

    return upsert_price(registry.get(METRIC), Decimal(amount))


def member(name):
    from toto.people.models import Person

    user = make_user(name)
    Person.objects.create(user=user, display_name=name)
    return user


class holding_one_gb:
    """Each of these users holds exactly one billable unit.

    The alternative is real VaultFiles, which drags MEDIA_ROOT and vault's
    byte-counting quirks into tests about a quadratic.

    BOTH halves of the provider are patched. `sample()` is what the nightly
    sweep walks and `measure()` is what a per-user estimate asks, and a test
    that patched only one would have the estimate and the sweep disagree — which
    is precisely the bug `test_the_estimate_agrees_with_the_sweep` exists to
    catch. A list, not a generator: the engine may iterate it more than once.
    """

    def __init__(self, *users):
        from unittest import mock

        held = [(user.pk, GB) for user in users]
        ids = {user.pk for user in users}
        self._patches = [
            mock.patch("toto.vault.taxes.StorageLevy.sample", return_value=held),
            mock.patch("toto.vault.taxes.StorageLevy.measure",
                       side_effect=lambda user: GB if user.pk in ids else 0),
        ]

    def __enter__(self):
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self._patches:
            patch.stop()
        return False


def hold(user, asset, display, code_suffix=""):
    """Give this user a balance, optionally in a second account of their own."""
    from toto.assets.models import AccountType, AssetHolding, LedgerAccount, to_base_units

    account = LedgerAccount.objects.create(
        code=f"user-{user.pk}{code_suffix}", name=f"{user.username}{code_suffix}",
        account_type=AccountType.USER, user=user, active=True)
    AssetHolding.objects.create(
        asset=asset, account=account,
        balance_base_units=to_base_units(Decimal(display), asset.decimals))
    return account


class ShapeTests(TestCase):
    """k × share², and nothing else."""

    def setUp(self):
        self.rule = TaxRule.objects.create(
            metric_code=METRIC, unit_label="GB", active=True,
            concentration_k=Decimal("100"))

    def test_no_share_adds_nothing(self):
        self.assertEqual(services._concentration_extra(self.rule, None),
                         Decimal("0"))

    def test_the_published_table(self):
        for share, expected in ((Decimal("0.01"), Decimal("0.01")),
                                (Decimal("0.10"), Decimal("1")),
                                (Decimal("0.50"), Decimal("25"))):
            self.assertEqual(services._concentration_extra(self.rule, share),
                             expected, share)

    def test_k_bounds_it(self):
        # Holding everything costs exactly k on top. k IS the cap; there is no
        # second ceiling to keep in step with it.
        self.assertEqual(services._concentration_extra(self.rule, Decimal("1")),
                         Decimal("100"))

    def test_k_zero_is_off(self):
        self.rule.concentration_k = Decimal("0")
        self.assertEqual(
            services._concentration_extra(self.rule, Decimal("0.9")),
            Decimal("0"))

    def test_it_is_smooth_near_the_bottom(self):
        """No threshold to sit under — the flaw in the surplus tax it replaces."""
        tiny = services._concentration_extra(self.rule, Decimal("0.001"))
        small = services._concentration_extra(self.rule, Decimal("0.002"))
        self.assertLess(tiny, small)
        self.assertLess(tiny, Decimal("0.001"))


class ShareResolutionTests(TestCase):
    def setUp(self):
        self.asset = make_gas_asset()
        self.rule = TaxRule.objects.create(
            metric_code=METRIC, unit_label="GB", active=True,
            concentration_k=Decimal("100"))

    def test_share_is_of_what_users_hold_not_the_engraved_supply(self):
        """Circulating, not total — or the dial sleeps for years.

        ASR is engraved at 6,666 and almost all of it sits in the reserve. A
        share of THAT makes every real holder look like a rounding error.
        """
        whale = member("whale")
        minnow = member("minnow")
        hold(whale, self.asset, "75")
        hold(minnow, self.asset, "25")

        shares = services._concentration_shares(self.rule, [whale.pk, minnow.pk])

        self.assertEqual(shares[whale.pk], Decimal("0.75"))
        self.assertEqual(shares[minnow.pk], Decimal("0.25"))

    def test_splitting_across_your_own_accounts_changes_nothing(self):
        splitter = member("splitter")
        hold(splitter, self.asset, "25", "-a")
        hold(splitter, self.asset, "25", "-b")
        hold(splitter, self.asset, "25", "-c")
        other = member("other")
        hold(other, self.asset, "25")

        shares = services._concentration_shares(self.rule, [splitter.pk, other.pk])

        self.assertEqual(shares[splitter.pk], Decimal("0.75"))

    def test_k_zero_costs_no_queries(self):
        self.rule.concentration_k = Decimal("0")
        user = member("someone")
        hold(user, self.asset, "10")

        with self.assertNumQueries(0):
            self.assertEqual(services._concentration_shares(self.rule, [user.pk]), {})

    def test_the_query_count_does_not_grow_with_the_platform(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        few, many = [], []
        for n in range(2):
            user = member(f"few-{n}")
            hold(user, self.asset, "1")
            few.append(user.pk)
        for n in range(30):
            user = member(f"many-{n}")
            hold(user, self.asset, "1")
            many.append(user.pk)

        with CaptureQueriesContext(connection) as small:
            services._concentration_shares(self.rule, few)
        with CaptureQueriesContext(connection) as large:
            services._concentration_shares(self.rule, many)

        self.assertEqual(len(small.captured_queries),
                         len(large.captured_queries))

    def test_an_empty_ledger_degrades_to_no_term(self):
        user = member("broke")
        self.assertEqual(services._concentration_shares(self.rule, [user.pk]), {})

    def test_a_broken_ledger_never_takes_the_run_down(self):
        from unittest import mock

        user = member("someone")
        hold(user, self.asset, "10")
        with mock.patch("toto.tariffs.rate_card.gas_asset",
                        side_effect=RuntimeError("ledger on fire")):
            self.assertEqual(
                services._concentration_shares(self.rule, [user.pk]), {})


class EndToEndTests(TestCase):
    """What the nightly run actually bills."""

    def setUp(self):
        self.rule = TaxRule.objects.create(
            metric_code=METRIC, unit_label="GB", active=True)
        self.asset = make_gas_asset()
        price_storage("1")

    def _billed(self):
        from toto.vault.models import VaultUsageEvent

        return {e.user_id: Decimal(str(e.quantity))
                for e in VaultUsageEvent.objects.filter(metric_code=METRIC)}

    def test_a_whale_pays_the_term_on_top_of_what_it_holds(self):
        """The whole point of adding rather than multiplying.

        The term is charged on the SHARE of circulating currency, not on the
        resource — so however little of the resource somebody holds, and
        whatever concession they have on it, accumulating still costs. A
        regulator you can opt out of by holding nothing is not a regulator, and
        the actor you least want to exempt is exactly the one who would.
        """
        self.rule.concentration_k = Decimal("100")
        self.rule.save(update_fields=["concentration_k"])

        whale = member("whale")
        hold(whale, self.asset, "100")

        with holding_one_gb(whale):
            services.levy_rule(self.rule)

        # One GB held, plus k × 1² for holding every circulating coin.
        self.assertEqual(self._billed()[whale.pk], Decimal("101"))

    def test_someone_with_no_money_pays_only_what_they_hold(self):
        self.rule.concentration_k = Decimal("100")
        self.rule.save(update_fields=["concentration_k"])

        plain = member("plain")
        whale = member("whale")
        hold(whale, self.asset, "100")

        with holding_one_gb(plain, whale):
            services.levy_rule(self.rule)

        self.assertEqual(self._billed()[plain.pk], Decimal("1"))

    def test_k_zero_bills_exactly_what_was_measured(self):
        whale = member("whale")
        hold(whale, self.asset, "100")

        with holding_one_gb(whale):
            services.levy_rule(self.rule)

        self.assertEqual(self._billed()[whale.pk], Decimal("1"))

    def test_the_audit_trail_explains_the_bigger_bill(self):
        from toto.vault.models import VaultUsageEvent

        self.rule.concentration_k = Decimal("100")
        self.rule.save(update_fields=["concentration_k"])
        whale = member("whale")
        hold(whale, self.asset, "100")

        with holding_one_gb(whale):
            services.levy_rule(self.rule)

        event = VaultUsageEvent.objects.get(user=whale)
        self.assertEqual(Decimal(event.metadata["concentration_share"]),
                         Decimal("1"))
        self.assertEqual(Decimal(event.metadata["concentration_k"]),
                         Decimal("100"))

    def test_the_estimate_agrees_with_the_sweep(self):
        self.rule.concentration_k = Decimal("100")
        self.rule.save(update_fields=["concentration_k"])
        whale = member("whale")
        hold(whale, self.asset, "100")

        with holding_one_gb(whale):
            rows = services.estimate_for_user(whale)
            row = next(r for r in rows if r["rule"].metric_code == METRIC)
            services.levy_rule(self.rule)

        self.assertEqual(row["billable"], self._billed()[whale.pk])
