"""The tariffs desk's writes and filters: posting a usage record by hand (free,
short, paid, already posted), a tariff's owner beside staff, the simulator's
answer, and the usage list's filters narrowing only the caller's own rows.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from toto.assets.models import AccountType, AssetHolding, LedgerAccount
from toto.assets.testing import LedgerTestCase as TestCase
from toto.assets.testing import make_asset
from toto.tariffs.models import (BillingMetric, Tariff, TariffItem, TariffStatus, UsageRecord,
                                 UsageStatus)

User = get_user_model()


class DeskTestCase(TestCase):
    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test", defaults={"author": "t", "publication_year": 2026, "active": True})
        self.ada = User.objects.create_user("ada", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")
        self.staff = User.objects.create_user("stan", password="pw", is_staff=True)
        self.asset = make_asset(unit_name="DSK", decimals=2, active=True)
        self.revenue = LedgerAccount.objects.create(code="DSK-REV", name="rev",
                                                    account_type=AccountType.SYSTEM, active=True)
        self.tariff = Tariff.objects.create(name="Desk", code="DSK-T", status=TariffStatus.ACTIVE)
        metric = BillingMetric.objects.create(code="dsk.op", label="dsk.op", active=True)
        TariffItem.objects.create(tariff=self.tariff, name="op", metric=metric,
                                  charged_asset=self.asset, price_per_unit_display=Decimal("2"),
                                  receiving_account=self.revenue)
        self.ada_account = LedgerAccount.objects.create(
            code="DSK-ADA", name="ada", user=self.ada, account_type=AccountType.USER, active=True)
        self.bob_account = LedgerAccount.objects.create(
            code="DSK-BOB", name="bob", user=self.bob, account_type=AccountType.USER, active=True)

    def record(self, account=None, metric="dsk.op", quantity=1, status=UsageStatus.PENDING, **extra):
        return UsageRecord.objects.create(
            tariff=self.tariff, payer_account=account or self.ada_account, metric_code=metric,
            quantity=Decimal(quantity), unit="", status=status,
            occurred_at=extra.pop("occurred_at", timezone.now()), **extra)

    def messages_of(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


class UsagePostTests(DeskTestCase):
    def post(self, record, user=None):
        self.client.force_login(user or self.staff)
        return self.client.post(reverse("tariffs:usage_post", args=[record.uuid]))

    def test_a_member_may_not_post_a_charge_even_their_own(self):
        record = self.record()
        self.assertEqual(self.post(record, self.ada).status_code, 403)
        record.refresh_from_db()
        self.assertEqual(record.status, UsageStatus.PENDING)

    def test_only_a_post_posts(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("tariffs:usage_post", args=[self.record().uuid]))
        self.assertEqual(response.status_code, 405)

    def test_an_unpriced_metric_posts_as_free_not_failed(self):
        record = self.record(metric="dsk.unpriced")
        response = self.post(record)
        self.assertRedirects(response, reverse("tariffs:usage_detail", args=[record.uuid]),
                             fetch_redirect_response=False)
        record.refresh_from_db()
        self.assertEqual((record.status, record.ledger_transaction_id), (UsageStatus.POSTED, None))
        self.assertIn("Usage posted. Nothing was charged — this metric is free.",
                      self.messages_of(response))

    def test_a_payer_who_cannot_pay_is_failed_with_the_reason(self):
        record = self.record()
        response = self.post(record)
        record.refresh_from_db()
        self.assertEqual(record.status, UsageStatus.FAILED)
        self.assertTrue(any("Insufficient DSK" in m for m in self.messages_of(response)))

    def test_a_funded_payer_is_charged_and_told_the_reference(self):
        AssetHolding.objects.create(account=self.ada_account, asset=self.asset,
                                    balance_base_units=1000)
        record = self.record(quantity=3)
        response = self.post(record)
        record.refresh_from_db()
        self.assertEqual(record.status, UsageStatus.POSTED)
        self.assertTrue(any(record.ledger_transaction.reference in m
                            for m in self.messages_of(response)))
        self.assertEqual(AssetHolding.objects.get(account=self.ada_account).balance_base_units, 400)

    def test_an_already_posted_record_is_left_alone(self):
        record = self.record(status=UsageStatus.POSTED)
        response = self.post(record)
        self.assertIn("Usage already posted.", self.messages_of(response))

    def test_a_failed_record_can_be_retried_once_funded(self):
        record = self.record()
        self.post(record)
        AssetHolding.objects.update_or_create(account=self.ada_account, asset=self.asset,
                                              defaults={"balance_base_units": 500})
        self.post(record)
        record.refresh_from_db()
        self.assertEqual(record.status, UsageStatus.POSTED)
        self.assertEqual(AssetHolding.objects.get(account=self.ada_account).balance_base_units, 300)


class OwnershipTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        self.owned = Tariff.objects.create(name="Ada's", code="ADA-T", owner=self.ada,
                                           status=TariffStatus.DRAFT)

    def test_the_owner_may_edit_their_tariff(self):
        self.client.force_login(self.ada)
        self.assertEqual(self.client.get(reverse("tariffs:tariff_edit", args=[self.owned.uuid])).status_code,
                         200)

    def test_another_member_may_not(self):
        self.client.force_login(self.bob)
        self.assertEqual(self.client.get(reverse("tariffs:tariff_edit", args=[self.owned.uuid])).status_code,
                         403)
        self.assertEqual(self.client.get(
            reverse("tariffs:tariff_item_create", args=[self.owned.uuid])).status_code, 403)

    def test_a_member_may_not_edit_the_ownerless_platform_tariff(self):
        self.client.force_login(self.ada)
        self.assertEqual(self.client.get(reverse("tariffs:tariff_edit", args=[self.tariff.uuid])).status_code,
                         403)

    def test_a_member_may_not_edit_an_item_on_it_either(self):
        item = self.tariff.items.get()
        self.client.force_login(self.ada)
        response = self.client.post(reverse("tariffs:tariff_item_edit", args=[item.pk]),
                                    {"price_per_unit_display": "0"})
        self.assertEqual(response.status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.price_per_unit_display, Decimal("2"))

    def test_the_owner_may_simulate_their_own_tariff(self):
        metric = BillingMetric.objects.get(code="dsk.op")
        TariffItem.objects.create(tariff=self.owned, name="op", metric=metric,
                                  charged_asset=self.asset, price_per_unit_display=Decimal("0.5"),
                                  receiving_account=self.revenue)
        self.client.force_login(self.ada)
        response = self.client.post(reverse("tariffs:tariff_simulate", args=[self.owned.uuid]),
                                    {"metric_code": "dsk.op", "quantity": "4", "unit": ""})
        self.assertEqual(response.status_code, 200)
        result = response.context["result"]
        self.assertEqual(result["totals_by_asset"]["DSK"]["base_units"], 200)

    def test_a_member_may_not_create_a_usage_record(self):
        self.client.force_login(self.ada)
        self.assertEqual(self.client.get(reverse("tariffs:usage_create")).status_code, 403)


class UsageListFilterTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        yesterday = timezone.now() - timezone.timedelta(days=1)
        self.old = self.record(metric="dsk.op", occurred_at=yesterday - timezone.timedelta(days=5),
                               source_type="vault.VaultFile", source_id="17")
        self.failed = self.record(metric="dsk.other", status=UsageStatus.FAILED)
        self.bobs = self.record(account=self.bob_account)

    def listed(self, user, **params):
        self.client.force_login(user)
        response = self.client.get(reverse("tariffs:usage_list"), params)
        self.assertEqual(response.status_code, 200)
        return {r.pk for r in response.context["usage_records"]}

    def test_a_member_filters_within_their_own_records(self):
        self.assertEqual(self.listed(self.ada, metric="dsk"), {self.old.pk, self.failed.pk})
        self.assertEqual(self.listed(self.ada, status="failed"), {self.failed.pk})

    def test_the_free_text_search_reaches_the_source(self):
        self.assertEqual(self.listed(self.ada, q="VaultFile"), {self.old.pk})
        self.assertEqual(self.listed(self.ada, q="17"), {self.old.pk})

    def test_the_date_window_bounds_the_list(self):
        since = (timezone.now() - timezone.timedelta(days=2)).date().isoformat()
        self.assertEqual(self.listed(self.ada, date_from=since), {self.failed.pk})
        self.assertEqual(self.listed(self.ada, date_to=since), {self.old.pk})

    def test_staff_filter_across_everybody_by_tariff(self):
        self.assertEqual(self.listed(self.staff, tariff="DSK-T"),
                         {self.old.pk, self.failed.pk, self.bobs.pk})
        self.assertEqual(self.listed(self.staff, tariff="NOPE"), set())

    def test_a_member_gets_no_tariff_picker(self):
        self.client.force_login(self.ada)
        response = self.client.get(reverse("tariffs:usage_list"))
        self.assertEqual(list(response.context["tariffs"]), [])
        self.assertFalse(response.context["is_manager"])


class TariffListFilterTests(DeskTestCase):
    def test_the_list_filters_by_status_and_text(self):
        Tariff.objects.create(name="Archive", code="OLD-T", status=TariffStatus.ARCHIVED)
        self.client.force_login(self.ada)
        response = self.client.get(reverse("tariffs:tariff_list"), {"status": "archived"})
        self.assertEqual([t.code for t in response.context["tariffs"]], ["OLD-T"])
        response = self.client.get(reverse("tariffs:tariff_list"), {"q": "desk"})
        self.assertEqual([t.code for t in response.context["tariffs"]], ["DSK-T"])
