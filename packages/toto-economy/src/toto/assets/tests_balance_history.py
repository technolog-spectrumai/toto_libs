"""The wallet's balance history, and the one property it must never break.

The chart sits directly beneath the balance cards. If its last point disagrees
with the number printed above it, the page contradicts itself — which is worse
than having no chart at all. Every test here exists to pin that down, because
the obvious implementation (sum the entries forwards from zero) does NOT have
that property:

``AssetHolding.balance_base_units`` is a cache, not a projection. It is
maintained by hand beside each entry write in four modules, there is no
recompute-from-entries anywhere in the tree, and the Django admin can set it
directly with no entry written. So the series is anchored on the holding and
walked BACKWARDS through the movements.
"""

import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from toto.assets.testing import TEST_ISSUER_KEY
from toto.assets.testing import LedgerTestCase as TestCase

from .models import (
    AssetHolding,
    LedgerAccount,
    LedgerEntry,
    LedgerTransaction,
    TransactionType,
)
from .services.assets import engrave_currency
from .views import BALANCE_HISTORY_DAYS, _balance_history_json

User = get_user_model()


def _asset(code="ASR", decimals=2):
    """An engraved currency with no supply — a legitimate state, and the one
    these tests want.

    ENGRAVE, not ``create_currency``: the latter also MINTS an opening supply,
    which would put entries and a reserve holding into every fixture here. These
    tests are arithmetic over movements, so the only movements must be the ones
    the test wrote. A bare ``Asset.objects.create`` is not an option either —
    the model carries a genesis-hash CHECK constraint, because every asset here
    carries provenance.
    """
    return engrave_currency(name=code, unit_name=code, code=code,
                            max_supply=Decimal("1000000"), decimals=decimals)


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY)
class BalanceHistoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("holder", password="pw")
        self.account = LedgerAccount.objects.create(
            code="w-1", name="w-1", account_type="user", active=True,
            user=self.user)
        self.asset = _asset()
        self.today = timezone.localdate()

    def _hold(self, base_units, asset=None):
        return AssetHolding.objects.create(
            asset=asset or self.asset, account=self.account,
            balance_base_units=base_units)

    def _move(self, base_units, *, days_ago=0, asset=None, reference=None):
        """One entry, backdated. created_at is auto_now_add, so it is written
        first and then forced — which is what any historical fixture must do."""
        tx = LedgerTransaction.objects.create(
            reference=reference or f"ref-{LedgerTransaction.objects.count()}",
            transaction_type=TransactionType.ASSET_TRANSFER)
        entry = LedgerEntry.objects.create(
            transaction=tx, account=self.account, asset=asset or self.asset,
            amount_base_units=base_units)
        when = timezone.now() - timedelta(days=days_ago)
        LedgerEntry.objects.filter(pk=entry.pk).update(created_at=when)
        return entry

    def _series(self, label=None):
        """The closing balance of each day, oldest first.

        Index convention, since every assertion below leans on it:
        ``series[-1]`` is the close of TODAY, ``series[-2]`` yesterday, and in
        general ``series[-(n + 1)]`` is the close of the day ``n`` days ago.
        A movement made on a given day is already inside that day's close — it
        is the day BEFORE which shows the level without it.
        """
        payload = json.loads(_balance_history_json(self.user, [self.account.pk]))
        datasets = payload["datasets"]
        if label is None:
            self.assertEqual(len(datasets), 1, datasets)
            return datasets[0]["data"]
        return next(d["data"] for d in datasets if d["label"] == label)

    # -- the property that matters ------------------------------------------

    def test_the_last_point_is_the_balance_on_the_card(self):
        """Anchored, not summed. This is the whole design."""
        self._hold(12_345)
        self._move(500, days_ago=1)

        self.assertEqual(self._series()[-1], 123.45)

    def test_it_still_matches_when_the_holding_disagrees_with_the_entries(self):
        """A holding edited in the admin writes no entry. Summing forwards
        would put the whole curve — including today — at the wrong level."""
        self._hold(100_000)
        self._move(300, days_ago=2)

        self.assertEqual(self._series()[-1], 1000.00)

    def test_yesterday_is_today_less_what_moved_today(self):
        self._hold(10_000)
        self._move(2_500, days_ago=0)

        series = self._series()
        self.assertEqual(series[-1], 100.00)
        self.assertEqual(series[-2], 75.00)

    def test_money_going_out_walks_the_other_way(self):
        """Amounts are signed; there is no direction field to branch on."""
        self._hold(10_000)
        self._move(-2_500, days_ago=0)

        series = self._series()
        self.assertEqual(series[-1], 100.00)
        self.assertEqual(series[-2], 125.00)

    def test_a_quiet_day_carries_the_previous_value_forward(self):
        """A flat line is correct. A gap or a zero would say the money left."""
        self._hold(10_000)
        self._move(1_000, days_ago=5)

        series = self._series()
        self.assertEqual(series[-1], 100.00)
        self.assertEqual(series[-2], 100.00)
        self.assertEqual(series[-5], 100.00)
        # The day it arrived closes at the new level…
        self.assertEqual(series[-6], 100.00)
        # …and only the day before shows the old one.
        self.assertEqual(series[-7], 90.00)

    # -- shape ---------------------------------------------------------------

    def test_the_axis_is_a_real_calendar(self):
        self._hold(1)
        payload = json.loads(_balance_history_json(self.user, [self.account.pk]))

        self.assertEqual(len(payload["labels"]), BALANCE_HISTORY_DAYS)
        self.assertEqual(len(payload["datasets"][0]["data"]),
                         BALANCE_HISTORY_DAYS)
        self.assertEqual(payload["labels"][-1], self.today.strftime("%m-%d"))

    def test_it_is_a_json_string_the_shared_partial_can_parse(self):
        """oya/partials/chart.html does JSON.parse on this — a dict would be
        rendered as a Python repr and throw."""
        self._hold(1)
        raw = _balance_history_json(self.user, [self.account.pk])

        self.assertIsInstance(raw, str)
        payload = json.loads(raw)
        self.assertEqual(payload["chart_type"], "line")
        self.assertIn("labels", payload)
        self.assertIn("datasets", payload)

    def test_every_point_is_a_number_and_not_a_decimal_string(self):
        """Chart.js will not plot a string, and a Decimal serialises as one."""
        self._hold(10_000)
        self._move(250, days_ago=3)

        for point in self._series():
            self.assertIsInstance(point, float)

    def test_one_line_per_asset(self):
        other = _asset(code="GLD", decimals=3)
        self._hold(10_000)
        self._hold(2_000, asset=other)

        payload = json.loads(_balance_history_json(self.user, [self.account.pk]))

        self.assertEqual({d["label"] for d in payload["datasets"]},
                         {"ASR", "GLD"})

    def test_decimals_are_per_asset(self):
        other = _asset(code="GLD", decimals=3)
        self._hold(10_000)
        self._hold(2_000, asset=other)

        self.assertEqual(self._series("ASR")[-1], 100.0)
        self.assertEqual(self._series("GLD")[-1], 2.0)

    def test_an_asset_spent_to_nothing_still_gets_a_line(self):
        """No holding row, but movements in the window — "you had some and now
        you do not" is exactly what somebody opens a history for."""
        self._move(-500, days_ago=2)

        series = self._series()
        self.assertEqual(series[-1], 0.0)
        # Spent two days ago: that day closes empty, the day before had it.
        self.assertEqual(series[-3], 0.0)
        self.assertEqual(series[-4], 5.00)

    # -- empty and edge cases ------------------------------------------------

    def test_no_accounts_draws_nothing(self):
        """The partial does JSON.parse('') and throws, so the caller guards on
        an empty string — the producer has to return exactly that."""
        self.assertEqual(_balance_history_json(self.user, []), "")

    def test_no_holdings_and_no_movements_draws_nothing(self):
        self.assertEqual(
            _balance_history_json(self.user, [self.account.pk]), "")

    def test_movement_older_than_the_window_does_not_move_the_line(self):
        """It is already inside the anchor. Counting it again would double it."""
        self._hold(10_000)
        self._move(9_999, days_ago=BALANCE_HISTORY_DAYS + 5)

        series = self._series()
        self.assertEqual(series[-1], 100.00)
        self.assertEqual(series[0], 100.00)

    def test_several_movements_on_one_day_are_one_step(self):
        """The group-by must bucket by DAY. LedgerEntry.Meta.ordering puts
        created_at in the GROUP BY unless the aggregate re-orders, which would
        give one row per entry and a step per movement."""
        self._hold(10_000)
        self._move(1_000, days_ago=0, reference="a")
        self._move(2_000, days_ago=0, reference="b")
        self._move(-500, days_ago=0, reference="c")

        series = self._series()
        self.assertEqual(series[-1], 100.00)
        self.assertEqual(series[-2], 75.00)

    def test_another_users_account_is_not_in_the_series(self):
        stranger = User.objects.create_user("stranger", password="pw")
        theirs = LedgerAccount.objects.create(
            code="w-2", name="w-2", account_type="user", active=True,
            user=stranger)
        AssetHolding.objects.create(asset=self.asset, account=theirs,
                                    balance_base_units=999_999)
        self._hold(10_000)

        self.assertEqual(self._series()[-1], 100.00)

    def test_a_negative_balance_is_drawn_rather_than_clamped(self):
        """Accounts can allow negative balances; a floor would lie about one."""
        self._hold(-2_500)

        self.assertEqual(self._series()[-1], -25.00)
