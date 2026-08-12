"""The anti-concentration term: k × share², added to the head tax.

A fixed supply plus a stipend loop makes wealth concentration a real failure
mode — whoever accumulates can outbid everyone for scarce capacity forever, and
no rate limit stops that. This is the regulator, and these are the properties
that make it one rather than just a bigger bill.
"""

from decimal import Decimal

from toto.assets.testing import LedgerTestCase as TestCase

from .. import services
from ..models import TaxRule
from .factories import make_gas_asset, make_user


def price_head(amount="1"):
    from toto.quota.metrics import registry
    from toto.tariffs.rate_card import upsert_price

    return upsert_price(registry.get("civics.head"), Decimal(amount))


def member(name, *communities):
    from toto.people.models import Person

    user = make_user(name)
    person = Person.objects.create(user=user, display_name=name)
    for row in communities:
        person.communities.add(row)
    return user


def community(name, head_weight=None):
    from toto.socialhub.models import Community, CommunityPrivilege

    row = Community.objects.create(name=name)
    if head_weight is not None:
        CommunityPrivilege.objects.create(community=row,
                                          head_weight=Decimal(head_weight))
    return row


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
            metric_code="civics.head", unit_label="head", active=True,
            concentration_k=Decimal("100"))

    def test_no_share_adds_nothing(self):
        self.assertEqual(services._concentration_heads(self.rule, None),
                         Decimal("0"))

    def test_the_published_table(self):
        for share, expected in ((Decimal("0.01"), Decimal("0.01")),
                                (Decimal("0.10"), Decimal("1")),
                                (Decimal("0.50"), Decimal("25"))):
            self.assertEqual(services._concentration_heads(self.rule, share),
                             expected, share)

    def test_k_bounds_it(self):
        # Holding everything costs exactly k on top. k IS the cap; there is no
        # second ceiling to keep in step with it.
        self.assertEqual(services._concentration_heads(self.rule, Decimal("1")),
                         Decimal("100"))

    def test_k_zero_is_off(self):
        self.rule.concentration_k = Decimal("0")
        self.assertEqual(
            services._concentration_heads(self.rule, Decimal("0.9")),
            Decimal("0"))

    def test_it_is_smooth_near_the_bottom(self):
        """No threshold to sit under — the flaw in the surplus tax it replaces."""
        tiny = services._concentration_heads(self.rule, Decimal("0.001"))
        small = services._concentration_heads(self.rule, Decimal("0.002"))
        self.assertLess(tiny, small)
        self.assertLess(tiny, Decimal("0.001"))


class ShareResolutionTests(TestCase):
    def setUp(self):
        self.asset = make_gas_asset()
        self.rule = TaxRule.objects.create(
            metric_code="civics.head", unit_label="head", active=True,
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
            metric_code="civics.head", unit_label="head", active=True)
        self.asset = make_gas_asset()
        price_head("1")

    def _billed(self):
        from toto.socialhub.models import SocialhubUsageEvent

        return {e.user_id: Decimal(str(e.quantity))
                for e in SocialhubUsageEvent.objects.filter(
                    metric_code="civics.head")}

    def test_a_trusted_whale_still_pays(self):
        """The whole point of adding rather than multiplying.

        head_weight 0 exempts you from the head tax. It must not exempt you from
        the concentration term, or the regulator is opt-out by joining the right
        guild — and the actor you least want to exempt is exactly the one who
        would.
        """
        self.rule.concentration_k = Decimal("100")
        self.rule.save(update_fields=["concentration_k"])

        trusted = community("Federal Tribe", head_weight="0")
        whale = member("whale", trusted)
        hold(whale, self.asset, "100")

        services.levy_rule(self.rule)

        self.assertEqual(self._billed()[whale.pk], Decimal("100"))

    def test_an_ordinary_member_with_nothing_pays_only_their_head(self):
        self.rule.concentration_k = Decimal("100")
        self.rule.save(update_fields=["concentration_k"])

        plain = member("plain", community("Weavers"))
        whale = member("whale")
        hold(whale, self.asset, "100")

        services.levy_rule(self.rule)

        self.assertEqual(self._billed()[plain.pk], Decimal("1"))

    def test_k_zero_bills_exactly_the_head_weight(self):
        whale = member("whale", community("Weavers"))
        hold(whale, self.asset, "100")

        services.levy_rule(self.rule)

        self.assertEqual(self._billed()[whale.pk], Decimal("1"))

    def test_the_audit_trail_explains_the_bigger_bill(self):
        from toto.socialhub.models import SocialhubUsageEvent

        self.rule.concentration_k = Decimal("100")
        self.rule.save(update_fields=["concentration_k"])
        whale = member("whale")
        hold(whale, self.asset, "100")

        services.levy_rule(self.rule)

        event = SocialhubUsageEvent.objects.get(user=whale)
        self.assertEqual(Decimal(event.metadata["concentration_share"]),
                         Decimal("1"))
        self.assertEqual(Decimal(event.metadata["concentration_k"]),
                         Decimal("100"))

    def test_the_estimate_agrees_with_the_sweep(self):
        self.rule.concentration_k = Decimal("100")
        self.rule.save(update_fields=["concentration_k"])
        whale = member("whale")
        hold(whale, self.asset, "100")

        rows = services.estimate_for_user(whale)
        row = next(r for r in rows if r["rule"].metric_code == "civics.head")

        services.levy_rule(self.rule)

        self.assertEqual(row["billable"], self._billed()[whale.pk])
