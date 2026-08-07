"""Who may read what on the tariffs pages.

These screens were `@login_required` and nothing else, so any signed-in account
could read every other user's usage records, the platform's revenue per
receiving account, and a list of who was nearly out of gas. The split is:

* prices stay public — gas.md's protection against being charged without being
  asked is that the rate card is published, so hiding it would remove the
  guarantee;
* usage is scoped to the person who paid for it;
* anything aggregated across payers is staff-only.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.assets.models import AccountType, Asset, LedgerAccount, to_base_units
from toto.tariffs.models import (
    BillingMetric, BillingUnit, Tariff, TariffItem, TariffStatus,
    UsageRecord, UsageStatus,
)

User = get_user_model()


class TariffVisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # PageProcessor 404s without one, so every rendered page needs it.
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "test", "publication_year": 2026, "active": True},
        )
        cls.alice = User.objects.create_user("alice", password="pw")
        cls.bob = User.objects.create_user("bob", password="pw")
        cls.staff = User.objects.create_user("staff", password="pw", is_staff=True)

        cls.asset = Asset.objects.create(
            name="Gas", unit_name="GAS", decimals=9,
            total_supply_base_units=10 ** 15, active=True,
        )
        cls.revenue = LedgerAccount.objects.create(
            code="platform-usage-fees", name="Fees",
            account_type=AccountType.SYSTEM, active=True,
        )
        # The platform card is deliberately ownerless, so only staff manage it.
        cls.tariff = Tariff.objects.create(
            code="platform-default", name="Platform default",
            status=TariffStatus.ACTIVE,
        )
        unit = BillingUnit.objects.create(
            code="request", label="REQUEST", app_label="tariffs", active=True,
        )
        metric = BillingMetric.objects.create(
            code="storage.request", label="Upload", app_label="vault",
            default_unit=unit, active=True,
        )
        TariffItem.objects.create(
            tariff=cls.tariff, metric=metric, name="Upload",
            charged_asset=cls.asset, price_per_unit_display=Decimal("0.00001"),
            unit=unit, receiving_account=cls.revenue, active=True,
        )

        cls.alice_account = LedgerAccount.objects.create(
            code="user-prepaid-alice", name="Alice", user=cls.alice,
            account_type=AccountType.USER, active=True,
        )
        cls.bob_account = LedgerAccount.objects.create(
            code="user-prepaid-bob", name="Bob", user=cls.bob,
            account_type=AccountType.USER, active=True,
        )
        cls.alice_record = UsageRecord.objects.create(
            tariff=cls.tariff, payer_account=cls.alice_account,
            metric_code="storage.request", quantity=Decimal("1"),
            unit="request", status=UsageStatus.POSTED,
        )
        cls.bob_record = UsageRecord.objects.create(
            tariff=cls.tariff, payer_account=cls.bob_account,
            metric_code="storage.request", quantity=Decimal("1"),
            unit="request", status=UsageStatus.POSTED,
        )

    # -- usage is yours ----------------------------------------------------

    def test_usage_list_shows_only_your_own_records(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse("tariffs:usage_list"))
        self.assertEqual(response.status_code, 200)
        records = list(response.context["usage_records"])
        self.assertEqual(records, [self.alice_record])

    def test_usage_list_shows_everything_to_staff(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("tariffs:usage_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["usage_records"]), 2)

    def test_usage_detail_refuses_someone_elses_record(self):
        self.client.force_login(self.alice)
        response = self.client.get(
            reverse("tariffs:usage_detail", args=[self.bob_record.uuid])
        )
        self.assertEqual(response.status_code, 403)

    def test_usage_detail_allows_your_own_record(self):
        self.client.force_login(self.alice)
        response = self.client.get(
            reverse("tariffs:usage_detail", args=[self.alice_record.uuid])
        )
        self.assertEqual(response.status_code, 200)

    # -- aggregates are staff-only ----------------------------------------

    def test_metrics_is_staff_only(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse("tariffs:metrics")).status_code, 403)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("tariffs:metrics")).status_code, 200)

    def test_api_metrics_is_staff_only(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse("tariffs:api_metrics")).status_code, 403)

    def test_simulate_is_staff_only(self):
        url = reverse("tariffs:tariff_simulate", args=[self.tariff.uuid])
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(url).status_code, 403)

    # -- prices stay public ------------------------------------------------

    def test_prices_remain_readable_by_any_signed_in_user(self):
        """The published rate card is what makes silent charging acceptable."""
        self.client.force_login(self.alice)
        response = self.client.get(
            reverse("tariffs:tariff_detail", args=[self.tariff.uuid])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["items"]), 1)

    def test_tariff_detail_withholds_other_payers_activity(self):
        self.client.force_login(self.alice)
        response = self.client.get(
            reverse("tariffs:tariff_detail", args=[self.tariff.uuid])
        )
        self.assertFalse(response.context["is_owner"])
        self.assertEqual(list(response.context["recent_usage"]), [])
        self.assertIsNone(response.context["usage_stats"])

    def test_tariff_detail_shows_activity_to_staff(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("tariffs:tariff_detail", args=[self.tariff.uuid])
        )
        self.assertTrue(response.context["is_owner"])
        self.assertEqual(response.context["usage_stats"]["total"], 2)
