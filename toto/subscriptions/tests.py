from decimal import Decimal

from django.test import SimpleTestCase
from django.utils import timezone

from .models import BillingInterval
from .services import period_end_for


class SubscriptionPeriodTests(SimpleTestCase):
    def test_month_end_clamps(self):
        start = timezone.datetime(2026, 1, 31, 12, 0, tzinfo=timezone.get_current_timezone())
        end = period_end_for(start, BillingInterval.MONTHLY, 1)
        self.assertEqual(end.month, 2)
        self.assertIn(end.day, [28, 29])

    def test_weekly_interval(self):
        start = timezone.now()
        end = period_end_for(start, BillingInterval.WEEKLY, 2)
        self.assertEqual((end - start).days, 14)
