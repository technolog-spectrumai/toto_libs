"""The reading rules — pure arithmetic, no database."""

from decimal import Decimal

from django.test import SimpleTestCase

from toto.mana.status import band_of, eta_hours, pct_of, preselect, trend_of


class BandTests(SimpleTestCase):
    def test_zero_and_below_are_empty(self):
        self.assertEqual(band_of(0, 100), "empty")
        self.assertEqual(band_of(-1, 100), "empty")

    def test_a_quarter_is_low_and_just_above_is_ok(self):
        self.assertEqual(band_of(25, 100), "low")
        self.assertEqual(band_of(Decimal("25.01"), 100), "ok")

    def test_full_is_ok(self):
        self.assertEqual(band_of(100, 100), "ok")


class PctTests(SimpleTestCase):
    def test_clamped_to_the_bar(self):
        self.assertEqual(pct_of(150, 100), 100)
        self.assertEqual(pct_of(-5, 100), 0)
        self.assertEqual(pct_of(42, 100), 42)

    def test_no_maximum_is_an_empty_bar_not_a_division_error(self):
        self.assertEqual(pct_of(5, 0), 0)


class TrendTests(SimpleTestCase):
    def test_a_pinned_pool_has_no_trend(self):
        """An up-arrow on a full bar promises something that cannot happen."""
        self.assertEqual(trend_of(100, 100, 5), "flat")
        self.assertEqual(trend_of(0, 100, -5), "flat")

    def test_direction_follows_the_net_rate(self):
        self.assertEqual(trend_of(50, 100, 5), "up")
        self.assertEqual(trend_of(50, 100, -5), "down")
        self.assertEqual(trend_of(50, 100, 0), "flat")


class EtaTests(SimpleTestCase):
    def test_hours_to_full_round_up(self):
        self.assertEqual(eta_hours(90, 100, 4), (3, None))

    def test_hours_to_empty_round_up(self):
        self.assertEqual(eta_hours(10, 100, -4), (None, 3))

    def test_steady_or_pinned_has_no_eta(self):
        self.assertEqual(eta_hours(50, 100, 0), (None, None))
        self.assertEqual(eta_hours(100, 100, 4), (None, None))


class PreselectTests(SimpleTestCase):
    FILES = [
        {"pk": 1, "drain_per_day": "10"},
        {"pk": 2, "drain_per_day": "40"},
        {"pk": 3, "drain_per_day": "5"},
    ]

    def test_it_ticks_the_fewest_that_turn_the_pool_around(self):
        # Total drain 55 against 96 a day of refill: one file already does it,
        # and it must be the costliest one.
        self.assertEqual(preselect(self.FILES, 96), [2])

    def test_it_keeps_ticking_until_the_rest_drains_less_than_comes_back(self):
        # 55 drain vs 12 refill: 2 (40) leaves 15 ≥ 12, then 1 (10) leaves 5.
        self.assertEqual(preselect(self.FILES, 12), [2, 1])

    def test_never_nothing(self):
        self.assertEqual(preselect(self.FILES, 10_000), [2])

    def test_no_files_is_no_selection(self):
        self.assertEqual(preselect([], 96), [])
