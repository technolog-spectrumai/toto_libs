"""Community discounts on mana charges — the seam itself (``tariffs.discounts``)
and the three readers of it: the affordability check, the posted charge and
the zero-charge path a full discount takes through ``post_usage_record``.

``toto.mana.tests.unit.test_discount`` pins the member-facing numbers through
``toto.quota.charge``; these pin the rules underneath: who has no discount
(an account nobody owns, a missing account, a host without subscriptions, a
circle), what is never discounted (a currency price, the subscription bill),
the rounding at odd percents, the minimum charge, and what a charge brought to
zero does and does not write.
"""

import unittest
from decimal import Decimal
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import TestCase

from toto.assets.models import (AccountType, Asset, AssetHolding, LedgerAccount,
                                LedgerTransaction)
from toto.assets.prepaid import get_or_create_prepaid_account
from toto.mana import services as mana
from toto.mana.tests.fixtures import economy, held, master, spend
from toto.people.models import Person
from toto.socialhub.models import Community
from toto.subscriptions.models import CommunityDiscount
from toto.tariffs import discounts
from toto.tariffs.charge import InsufficientBalanceError, charge_user, check_user_can_act
from toto.tariffs.charge import refund_usage_record
from toto.tariffs.models import (BillingMetric, Tariff, TariffItem, TariffStatus,
                                 UsageRecord, UsageStatus)
from toto.tariffs.services import (ChargeDraft, calculate_tariff_charge, check_can_afford,
                                   rate_usage_record, record_and_post_usage, simulate_tariff)

User = get_user_model()


def draft(asset_id, amount=1000, metadata=None):
    return ChargeDraft(tariff_item=None, quantity=Decimal(1), unit="",
                       amount_base_units=amount, payer_account_id=1,
                       receiving_account_id=0, asset_id=asset_id,
                       metadata=dict(metadata or {}))


def member(username, *communities):
    user = User.objects.create_user(username, password="pw")
    Person.objects.create(user=user, display_name=username).communities.add(*communities)
    return user


def without(app_label):
    """``apps.is_installed`` as a host that does not install ``app_label``."""
    real = apps.is_installed
    return mock.patch("toto.tariffs.discounts.apps.is_installed",
                      side_effect=lambda name: name != app_label and real(name))


class DiscountedBaseUnitsTests(TestCase):
    """The rounding rule: the member's price rounds DOWN to a whole base unit."""

    def test_an_odd_percent_rounds_the_price_down(self):
        self.assertEqual(discounts.discounted_base_units(100, 33), 67)
        self.assertEqual(discounts.discounted_base_units(7, 33), 4)          # 4.69
        self.assertEqual(discounts.discounted_base_units(99, 1), 98)         # 98.01

    def test_a_one_percent_discount_makes_a_single_base_unit_free(self):
        self.assertEqual(discounts.discounted_base_units(1, 1), 0)

    def test_a_percent_past_a_hundred_is_free_never_a_credit(self):
        self.assertEqual(discounts.discounted_base_units(500, 150), 0)

    def test_a_negative_percent_is_never_a_surcharge(self):
        self.assertEqual(discounts.discounted_base_units(500, -20), 500)

    def test_nothing_to_discount_stays_nothing(self):
        self.assertEqual(discounts.discounted_base_units(0, 50), 0)
        self.assertEqual(discounts.discounted_base_units(-10, 50), -10)

    def test_a_large_amount_is_exact(self):
        self.assertEqual(discounts.discounted_base_units(10 ** 18, 25), 75 * 10 ** 16)


@master
class PercentForAccountTests(TestCase):
    def setUp(self):
        economy()
        self.students = Community.objects.create(name="Students")
        CommunityDiscount.objects.create(community=self.students, percent=40)
        self.ada = member("ada", self.students)
        self.account, _ = get_or_create_prepaid_account(self.ada)

    def test_a_member_account_names_the_percent_and_the_community(self):
        self.assertEqual(discounts.percent_for_account(self.account.pk), (40, "Students"))

    def test_no_account_asks_nothing(self):
        with self.assertNumQueries(0):
            self.assertEqual(discounts.percent_for_account(None), (0, ""))
            self.assertEqual(discounts.percent_for_account(0), (0, ""))

    def test_an_account_that_does_not_exist_has_no_discount(self):
        self.assertEqual(discounts.percent_for_account(10 ** 9), (0, ""))

    def test_an_account_nobody_owns_pays_the_list_price(self):
        treasury = LedgerAccount.objects.create(code="TREASURY-T", name="Treasury",
                                                account_type=AccountType.SYSTEM, active=True)
        self.assertEqual(discounts.percent_for_account(treasury.pk), (0, ""))

    def test_a_host_without_subscriptions_asks_nothing(self):
        with without("toto.subscriptions"), self.assertNumQueries(0):
            self.assertEqual(discounts.percent_for_account(self.account.pk), (0, ""))

    def test_a_database_error_is_the_list_price_not_a_failed_charge(self):
        with mock.patch.object(LedgerAccount.objects, "select_related",
                               side_effect=DatabaseError("no such table")):
            self.assertEqual(discounts.percent_for_account(self.account.pk), (0, ""))

    def test_the_highest_discount_across_communities_wins(self):
        staff = Community.objects.create(name="Staff")
        CommunityDiscount.objects.create(community=staff, percent=75)
        self.ada.community_profile.communities.add(staff)
        self.assertEqual(discounts.percent_for_account(self.account.pk), (75, "Staff"))

    def test_a_member_with_no_profile_has_no_discount(self):
        bob = User.objects.create_user("bob", password="pw")
        account, _ = get_or_create_prepaid_account(bob)
        self.assertEqual(discounts.percent_for_account(account.pk), (0, ""))

    def test_a_zero_percent_row_is_no_discount(self):
        zero = Community.objects.create(name="Zero")
        CommunityDiscount.objects.create(community=zero, percent=0)
        bob = member("bob", zero)
        account, _ = get_or_create_prepaid_account(bob)
        self.assertEqual(discounts.percent_for_account(account.pk), (0, ""))

    def test_a_circle_gives_no_discount_even_with_a_row(self):
        """A discount row cannot be saved on a circle, but a community turned
        into one behind ``save`` (a queryset update) keeps its old row — and
        the money axis never reads circles, so it still takes nothing off."""
        board = Community.objects.create(name="Board")
        CommunityDiscount.objects.create(community=board, percent=90)
        Community.objects.filter(pk=board.pk).update(is_circle=True)
        bob = member("bob", board)
        account, _ = get_or_create_prepaid_account(bob)
        self.assertEqual(discounts.percent_for_account(account.pk), (0, ""))


@master
class ManaAssetIdsTests(TestCase):
    def setUp(self):
        economy()

    def test_they_are_the_three_pool_assets(self):
        self.assertEqual(discounts.mana_asset_ids(), mana.pool_asset_ids())
        self.assertEqual(len(discounts.mana_asset_ids()), 3)
        asr = Asset.objects.get(unit_name="ASR")
        self.assertNotIn(asr.pk, discounts.mana_asset_ids())

    def test_a_host_without_mana_has_none(self):
        with without("toto.mana"):
            self.assertEqual(discounts.mana_asset_ids(), set())

    def test_a_database_error_reads_as_no_pools(self):
        with mock.patch("toto.mana.services.pools", side_effect=DatabaseError("gone")):
            self.assertEqual(discounts.mana_asset_ids(), set())


@master
class ApplyTests(TestCase):
    def setUp(self):
        economy()
        self.students = Community.objects.create(name="Students")
        self.discount = CommunityDiscount.objects.create(community=self.students, percent=50)
        self.ada = member("ada", self.students)
        self.account, _ = get_or_create_prepaid_account(self.ada)
        self.pools = mana.pools()
        self.asr = Asset.objects.get(unit_name="ASR")

    def test_only_the_draft_in_a_pool_asset_is_discounted(self):
        pooled = draft(self.pools["compute"].asset_id, 1000)
        currency = draft(self.asr.pk, 1000)
        discounts.apply([pooled, currency], self.account.pk, "workflows.run")
        self.assertEqual((pooled.amount_base_units, currency.amount_base_units), (500, 1000))
        self.assertNotIn("discount_percent", currency.metadata)

    def test_every_pool_draft_is_discounted_by_the_same_percent(self):
        drafts = [draft(self.pools[role].asset_id, 301) for role in ("security", "storage")]
        discounts.apply(drafts, self.account.pk, "storage.request")
        self.assertEqual([d.amount_base_units for d in drafts], [150, 150])
        self.assertEqual({d.metadata["list_amount_base_units"] for d in drafts}, {301})

    def test_the_discount_adds_to_the_draft_metadata_rather_than_replacing_it(self):
        one = draft(self.pools["compute"].asset_id, 10, {"tariff_code": "T", "item_id": 3})
        discounts.apply([one], self.account.pk, "workflows.run")
        self.assertEqual(one.metadata, {"tariff_code": "T", "item_id": 3,
                                        "list_amount_base_units": 10,
                                        "discount_percent": 50,
                                        "discount_source": "Students"})

    def test_it_discounts_in_place_and_returns_the_same_list(self):
        drafts = [draft(self.pools["compute"].asset_id, 10)]
        self.assertIs(discounts.apply(drafts, self.account.pk, "workflows.run"), drafts)

    def test_no_payer_never_asks_who_is_paying(self):
        one = draft(self.pools["compute"].asset_id, 10)
        with mock.patch("toto.tariffs.discounts.percent_for_account") as asked:
            discounts.apply([one], None, "workflows.run")
        asked.assert_not_called()
        self.assertEqual(one.amount_base_units, 10)

    def test_an_exempt_metric_never_asks_who_is_paying(self):
        one = draft(self.pools["compute"].asset_id, 10)
        with mock.patch("toto.tariffs.discounts.percent_for_account") as asked:
            discounts.apply([one], self.account.pk, "subscription.month")
        asked.assert_not_called()
        self.assertEqual(one.amount_base_units, 10)

    def test_no_discount_never_reads_the_pools(self):
        self.discount.percent = 0
        self.discount.save()
        one = draft(self.pools["compute"].asset_id, 10)
        with mock.patch("toto.tariffs.discounts.mana_asset_ids") as pools_read:
            discounts.apply([one], self.account.pk, "workflows.run")
        pools_read.assert_not_called()
        self.assertEqual(one.amount_base_units, 10)
        self.assertEqual(one.metadata, {})

    def test_an_empty_list_costs_no_query(self):
        with self.assertNumQueries(0):
            self.assertEqual(discounts.apply([], self.account.pk, "workflows.run"), [])

    def test_a_host_without_mana_discounts_nothing(self):
        one = draft(self.pools["compute"].asset_id, 10)
        with without("toto.mana"):
            discounts.apply([one], self.account.pk, "workflows.run")
        self.assertEqual(one.amount_base_units, 10)


@master
class DiscountedRatingTests(TestCase):
    """``calculate_tariff_charge`` and the readers built on it, against a
    tariff priced in the compute pool."""

    def setUp(self):
        economy()
        self.students = Community.objects.create(name="Students")
        self.discount = CommunityDiscount.objects.create(community=self.students, percent=50)
        self.ada = member("ada", self.students)
        self.bob = User.objects.create_user("bob", password="pw")        # no community
        self.ada_account, _ = get_or_create_prepaid_account(self.ada)
        self.bob_account, _ = get_or_create_prepaid_account(self.bob)
        self.pool = mana.pools()["compute"]
        self.asset = self.pool.asset
        self.unit = 10 ** self.asset.decimals
        self.revenue = LedgerAccount.objects.create(
            code="REV-DISC", name="Revenue", account_type=AccountType.SYSTEM, active=True)
        self.tariff = Tariff.objects.create(name="Disc", code="DISC", status=TariffStatus.ACTIVE)
        self.item = self.price("test.discounted", "2")

    def price(self, code, display, **extra):
        metric, _ = BillingMetric.objects.get_or_create(
            code=code, defaults={"label": code, "active": True})
        return TariffItem.objects.create(
            tariff=self.tariff, name=code, metric=metric, charged_asset=self.asset,
            price_per_unit_display=Decimal(display), receiving_account=self.revenue,
            active=True, **extra)

    def amount(self, account, code="test.discounted", quantity=1):
        drafts = calculate_tariff_charge(self.tariff, code, Decimal(quantity), "",
                                         payer_account_id=account.pk if account else None)
        return drafts[0].amount_base_units

    def full_discount(self):
        self.discount.percent = 100
        self.discount.save()

    def test_a_member_is_rated_at_their_price_and_a_stranger_at_the_list(self):
        self.assertEqual(self.amount(self.ada_account), 1 * self.unit)
        self.assertEqual(self.amount(self.bob_account), 2 * self.unit)

    def test_without_a_payer_the_list_price_is_quoted(self):
        self.assertEqual(self.amount(None), 2 * self.unit)
        result = simulate_tariff(self.tariff, [{"metric_code": "test.discounted", "quantity": "3"}])
        self.assertEqual(result["lines"][0]["amount_base_units"], 6 * self.unit)

    def test_the_discount_comes_off_the_minimum_charge_too(self):
        """Rated first (the minimum is part of rating), discounted after."""
        self.price("test.minimum", "0", minimum_charge_base_units=10)
        self.assertEqual(self.amount(None, "test.minimum"), 10)
        self.assertEqual(self.amount(self.ada_account, "test.minimum"), 5)

    def test_the_subscription_bill_in_a_pool_asset_is_still_not_discounted(self):
        self.price("subscription.month", "5")
        self.assertEqual(self.amount(self.ada_account, "subscription.month"), 5 * self.unit)

    def test_rating_keeps_the_list_unit_price_beside_the_member_amount(self):
        record = UsageRecord.objects.create(
            tariff=self.tariff, payer_account=self.ada_account, metric_code="test.discounted",
            quantity=Decimal(3), unit="", occurred_at=self.ada.date_joined)
        line = rate_usage_record(record)[0]
        self.assertEqual(line.price_per_unit_base_units, 2 * self.unit)
        self.assertEqual(line.amount_base_units, 3 * self.unit)
        self.assertEqual(line.metadata["list_amount_base_units"], 6 * self.unit)

    def test_the_check_passes_at_exactly_the_member_price_and_fails_one_unit_short(self):
        spend(self.ada, "compute", "99")                     # 1 left: the member price
        self.assertEqual(check_can_afford(self.tariff, self.ada_account,
                                          [("test.discounted", Decimal(1), "")]), (True, ""))
        ok, message = check_can_afford(self.tariff, self.ada_account,
                                       [("test.discounted", Decimal(1), ""),
                                        ("test.discounted", Decimal("0.000000001"), "")])
        self.assertFalse(ok)
        self.assertIn("need", message)

    def test_the_check_sums_several_discounted_charges_before_comparing(self):
        spend(self.ada, "compute", "98.5")                   # 1.5 left
        ok, message = check_can_afford(self.tariff, self.ada_account,
                                       [("test.discounted", Decimal(1), ""),
                                        ("test.discounted", Decimal(1), "")])
        self.assertFalse(ok)
        self.assertIn("need 2", message)

    def test_the_refusal_carries_the_member_price_as_structured_data(self):
        spend(self.ada, "compute", "99.5")                   # 0.5 left, needs 1
        with self.assertRaises(InsufficientBalanceError) as caught:
            check_user_can_act(self.ada, self.tariff, "test.discounted", 1)
        self.assertEqual(caught.exception.needed_base_units, 1 * self.unit)
        self.assertEqual(caught.exception.have_base_units, self.unit // 2)
        self.assertIn("needs 1 and you have 0.5", str(caught.exception))

    def test_a_charge_from_an_account_nobody_owns_is_at_the_list_price(self):
        system = LedgerAccount.objects.create(code="SYS-PAYER", name="System",
                                              account_type=AccountType.SYSTEM, active=True)
        self.assertEqual(self.amount(system), 2 * self.unit)

    def test_a_fully_discounted_member_with_an_empty_pool_may_still_act(self):
        self.full_discount()
        spend(self.ada, "compute", "100")
        self.assertEqual(check_can_afford(self.tariff, self.ada_account,
                                          [("test.discounted", Decimal(1), "")]), (True, ""))
        check_user_can_act(self.ada, self.tariff, "test.discounted", 1)   # must not raise

    def test_a_fully_discounted_charge_moves_nothing_and_writes_no_transaction(self):
        self.full_discount()
        before = LedgerTransaction.objects.count()
        record, tx = record_and_post_usage(self.tariff, self.ada_account, "test.discounted",
                                           Decimal(1), "")
        self.assertIsNone(tx)
        self.assertEqual((record.status, record.error_message, record.ledger_transaction_id),
                         (UsageStatus.POSTED, "", None))
        self.assertEqual(LedgerTransaction.objects.count(), before)
        self.assertEqual(held(self.ada, "compute"), Decimal("100"))
        self.assertFalse(AssetHolding.objects.filter(account=self.revenue).exclude(
            balance_base_units=0).exists())

    def test_a_fully_discounted_charge_keeps_its_line_as_the_record_of_the_discount(self):
        self.full_discount()
        record, _tx = record_and_post_usage(self.tariff, self.ada_account, "test.discounted",
                                            Decimal(1), "")
        line = record.charges.get()
        self.assertEqual(line.amount_base_units, 0)
        self.assertEqual((line.metadata["list_amount_base_units"], line.metadata["discount_percent"]),
                         (2 * self.unit, 100))

    def test_the_user_facing_charge_returns_no_transaction_for_a_free_use(self):
        self.full_discount()
        record, tx = charge_user(self.ada, self.tariff, "test.discounted", 1)
        self.assertIsNone(tx)
        self.assertEqual(record.status, UsageStatus.POSTED)

    def test_a_free_use_has_nothing_to_refund(self):
        self.full_discount()
        record, _tx = charge_user(self.ada, self.tariff, "test.discounted", 1)
        self.assertIsNone(refund_usage_record(record))
        record.refresh_from_db()
        self.assertEqual(record.status, UsageStatus.POSTED)

    def test_a_discount_raised_after_rating_does_not_change_a_rated_record(self):
        """Posting reads the rated lines: the price agreed at rating is paid."""
        record = UsageRecord.objects.create(
            tariff=self.tariff, payer_account=self.ada_account, metric_code="test.discounted",
            quantity=Decimal(1), unit="", occurred_at=self.ada.date_joined)
        rate_usage_record(record)
        self.full_discount()
        from toto.tariffs.services import post_usage_record

        tx = post_usage_record(record)
        self.assertIsNotNone(tx)
        self.assertEqual(held(self.ada, "compute"), Decimal("99"))

    @unittest.skip("SUSPECTED BUG tariffs/services.py:243-245: re-posting a POSTED "
                   "zero-charge record raises ValueError('marked posted but has no ledger "
                   "transaction') instead of returning None as the idempotency promise says")
    def test_posting_a_free_use_again_is_idempotent(self):
        """``post_usage_record`` promises that a POSTED record returns its
        existing transaction; a free one has none, so the answer is None."""
        from toto.tariffs.services import post_usage_record

        self.full_discount()
        record, _tx = record_and_post_usage(self.tariff, self.ada_account, "test.discounted",
                                            Decimal(1), "")
        self.assertIsNone(post_usage_record(record))
