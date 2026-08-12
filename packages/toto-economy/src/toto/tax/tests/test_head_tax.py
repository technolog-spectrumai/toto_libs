"""The head tax: everyone pays, and what you pay is your best community's rate.

The one levy that measures being a member rather than holding a thing. Its
whole mechanism is that the community's standing arrives as the QUANTITY the
provider reports — the price is one number for the entire platform.
"""

from decimal import Decimal

from django.utils import timezone

from toto.assets.testing import LedgerTestCase as TestCase

from .. import services
from ..models import TaxRule
from .factories import fund_prepaid, make_gas_asset, make_user


def price_head(amount):
    from toto.quota.metrics import registry as metric_registry
    from toto.tariffs.rate_card import upsert_price

    return upsert_price(metric_registry.get("civics.head"), Decimal(amount))


def community(name, head_weight=None):
    from toto.socialhub.models import Community, CommunityPrivilege

    row = Community.objects.create(name=name)
    if head_weight is not None:
        CommunityPrivilege.objects.create(
            community=row, head_weight=Decimal(head_weight))
    return row


def member(name, *communities):
    from toto.people.models import Person

    user = make_user(name)
    person = Person.objects.create(user=user, display_name=name)
    for row in communities:
        person.communities.add(row)
    return user


class HeadTaxTests(TestCase):
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

    def test_an_ordinary_member_pays_one_head(self):
        user = member("alice", community("Weavers"))
        fund_prepaid(user, self.asset, 10 ** 12)

        summary = services.levy_rule(self.rule)

        self.assertEqual(summary.counts, {services.Outcome.LEVIED: 1})
        self.assertEqual(self._billed()[user.pk], Decimal("1"))

    def test_someone_in_no_community_still_pays(self):
        """A tax on everyone, not a penalty for being affiliated."""
        user = member("hermit")
        fund_prepaid(user, self.asset, 10 ** 12)

        services.levy_rule(self.rule)

        self.assertEqual(self._billed()[user.pk], Decimal("1"))

    def test_a_trusted_community_exempts_its_members(self):
        """Weight 0 — what is_federal_tribe promised and never delivered."""
        user = member("noble", community("Federal Tribe", head_weight="0"))
        fund_prepaid(user, self.asset, 10 ** 12)

        services.levy_rule(self.rule)

        self.assertEqual(self._billed(), {})

    def test_a_less_trusted_community_pays_more(self):
        user = member("suspect", community("Probation", head_weight="3"))
        fund_prepaid(user, self.asset, 10 ** 12)

        services.levy_rule(self.rule)

        self.assertEqual(self._billed()[user.pk], Decimal("3"))

    def test_the_best_community_wins(self):
        """Belonging to a good community is what is supposed to be worth having."""
        user = member("both",
                      community("Probation", head_weight="3"),
                      community("Trusted", head_weight="0.5"))
        fund_prepaid(user, self.asset, 10 ** 12)

        services.levy_rule(self.rule)

        self.assertEqual(self._billed()[user.pk], Decimal("0.5"))

    def test_an_ordinary_community_caps_a_heavy_one(self):
        user = member("mixed",
                      community("Probation", head_weight="3"),
                      community("Weavers"))
        fund_prepaid(user, self.asset, 10 ** 12)

        services.levy_rule(self.rule)

        self.assertEqual(self._billed()[user.pk], Decimal("1"))

    def test_the_price_is_the_same_for_everybody(self):
        """The rate card has ONE row; only the quantity differs."""
        from toto.tariffs.models import TariffItem

        member("alice", community("Weavers"))
        member("suspect", community("Probation", head_weight="3"))

        items = TariffItem.objects.filter(metric__code="civics.head",
                                          active=True)
        self.assertEqual(items.count(), 1)

    def test_the_sweep_does_not_scale_with_the_platform(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        heavy = community("Probation", head_weight="3")
        for n in range(12):
            member(f"member-{n}", heavy)

        with CaptureQueriesContext(connection) as captured:
            list(services.levy_rule(self.rule).counts)

        socialhub_reads = [q for q in captured.captured_queries
                           if "socialhub_communityprivilege" in q["sql"]]
        self.assertEqual(len(socialhub_reads), 1, "\n".join(
            q["sql"] for q in socialhub_reads))

    def test_a_broke_member_is_warned_and_stays_a_member(self):
        from toto.people.models import Person

        user = member("broke", community("Weavers"))
        Person.objects.filter(user=user).update(display_name="broke")

        summary = services.levy_rule(self.rule)

        self.assertEqual(summary.counts, {services.Outcome.FAILED: 1})
        # Membership is not a thing that can be repossessed.
        self.assertTrue(user.community_profile.communities.exists())
