from decimal import Decimal
from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform
from toto.invoice.models import Invoice, InvoiceStatus, BillingCycle, BillingCycleFrequency
from toto.vault.models import Bucket, VaultDirectory


class InvoiceModelTest(TestCase):

    def setUp(self):
        self.staff = User.objects.create_user("staff", password="pass", is_staff=True)
        self.user = User.objects.create_user("alice", password="pass")

    def _make_invoice(self, **kwargs):
        defaults = dict(
            issued_to=self.user,
            issued_by=self.staff,
            title="Test Invoice",
            amount=Decimal("99.99"),
            currency_label="USD",
        )
        defaults.update(kwargs)
        return Invoice.objects.create(**defaults)

    def test_create_pending(self):
        inv = self._make_invoice()
        self.assertEqual(inv.status, InvoiceStatus.PENDING)
        self.assertIsNone(inv.paid_at)

    def test_str(self):
        inv = self._make_invoice()
        self.assertIn("alice", str(inv))
        self.assertIn("99.99", str(inv))
        self.assertIn("USD", str(inv))

    def test_is_paid_false_when_pending(self):
        inv = self._make_invoice()
        self.assertFalse(inv.is_paid)

    def test_is_paid_true_when_paid(self):
        inv = self._make_invoice(status=InvoiceStatus.PAID, paid_at=timezone.now())
        self.assertTrue(inv.is_paid)

    def test_is_overdue_true_when_past_due(self):
        from datetime import date, timedelta
        inv = self._make_invoice(
            status=InvoiceStatus.PENDING,
            due_date=date.today() - timedelta(days=1),
        )
        self.assertTrue(inv.is_overdue)

    def test_is_overdue_false_when_paid(self):
        from datetime import date, timedelta
        inv = self._make_invoice(
            status=InvoiceStatus.PAID,
            due_date=date.today() - timedelta(days=1),
            paid_at=timezone.now(),
        )
        self.assertFalse(inv.is_overdue)

    def test_is_overdue_false_when_future_due(self):
        from datetime import date, timedelta
        inv = self._make_invoice(
            status=InvoiceStatus.PENDING,
            due_date=date.today() + timedelta(days=10),
        )
        self.assertFalse(inv.is_overdue)

    def test_no_due_date_not_overdue(self):
        inv = self._make_invoice(due_date=None)
        self.assertFalse(inv.is_overdue)

    def test_cancelled_not_overdue(self):
        from datetime import date, timedelta
        inv = self._make_invoice(
            status=InvoiceStatus.CANCELLED,
            due_date=date.today() - timedelta(days=5),
        )
        self.assertFalse(inv.is_overdue)

    def test_invoices_isolated_from_assets(self):
        """Invoice has no FK to any assets model."""
        from django.db import models as _m
        fk_targets = [
            f.related_model.__name__
            for f in Invoice._meta.get_fields()
            if isinstance(f, (_m.ForeignKey, _m.OneToOneField))
            and f.related_model is not None
        ]
        for name in fk_targets:
            self.assertNotIn(
                "assets", name.lower(),
                msg=f"Invoice FK targets an assets model: {name}",
            )

    def test_multiple_invoices_for_same_user(self):
        self._make_invoice(title="Invoice A", amount=Decimal("10.00"))
        self._make_invoice(title="Invoice B", amount=Decimal("20.00"))
        self.assertEqual(Invoice.objects.filter(issued_to=self.user).count(), 2)

    def test_no_issued_by_allowed(self):
        inv = self._make_invoice(issued_by=None)
        self.assertIsNone(inv.issued_by)


class BillingCycleModelTest(TestCase):

    def test_hours_per_period(self):
        cases = [
            (BillingCycleFrequency.DAILY, 24),
            (BillingCycleFrequency.WEEKLY, 168),
            (BillingCycleFrequency.MONTHLY, 730),
            (BillingCycleFrequency.YEARLY, 8760),
        ]
        for freq, expected in cases:
            cycle = BillingCycle(name=f"test-{freq}", frequency=freq)
            self.assertEqual(cycle.hours_per_period, expected)

    def test_str(self):
        cycle = BillingCycle.objects.create(name="Monthly Billing", frequency=BillingCycleFrequency.MONTHLY)
        self.assertIn("Monthly Billing", str(cycle))


class InvoiceListViewTest(TestCase):

    def setUp(self):
        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.staff = User.objects.create_user("staff", password="pass", is_staff=True)
        self.alice = User.objects.create_user("alice", password="pass")
        self.bob = User.objects.create_user("bob", password="pass")
        self.client = Client()

    def _make_invoice(self, user, **kwargs):
        defaults = dict(
            issued_to=user,
            issued_by=self.staff,
            title="Invoice",
            amount=Decimal("50.00"),
            currency_label="PLN",
        )
        defaults.update(kwargs)
        return Invoice.objects.create(**defaults)

    def test_requires_login(self):
        url = reverse("invoice:invoice_list")
        response = self.client.get(url)
        self.assertNotEqual(response.status_code, 200)

    def test_shows_own_invoices(self):
        self._make_invoice(self.alice, title="Alice Invoice")
        self._make_invoice(self.bob, title="Bob Invoice")
        self.client.login(username="alice", password="pass")
        url = reverse("invoice:invoice_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        rows = response.context["invoice_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["invoice"].issued_to, self.alice)

    def test_pending_and_paid_counts(self):
        self._make_invoice(self.alice, status=InvoiceStatus.PENDING)
        self._make_invoice(self.alice, status=InvoiceStatus.PAID, paid_at=timezone.now())
        self.client.login(username="alice", password="pass")
        response = self.client.get(reverse("invoice:invoice_list"))
        self.assertEqual(response.context["pending_count"], 1)
        self.assertEqual(response.context["paid_count"], 1)

    def test_yaml_in_invoice_rows(self):
        self._make_invoice(self.alice)
        self.client.login(username="alice", password="pass")
        response = self.client.get(reverse("invoice:invoice_list"))
        rows = response.context["invoice_rows"]
        self.assertIn("invoice:", rows[0]["yaml"])

    def test_invoice_rows_not_cross_user(self):
        self._make_invoice(self.bob, title="Bob Only")
        self.client.login(username="alice", password="pass")
        response = self.client.get(reverse("invoice:invoice_list"))
        self.assertEqual(len(response.context["invoice_rows"]), 0)


class InvoiceMetricsViewTest(TestCase):

    def setUp(self):
        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.user = User.objects.create_user("alice", password="pass")
        self.client = Client()

    def _make_invoice(self, **kwargs):
        defaults = dict(issued_to=self.user, title="Inv", amount=Decimal("10.00"), currency_label="USD")
        defaults.update(kwargs)
        return Invoice.objects.create(**defaults)

    def test_requires_login(self):
        response = self.client.get(reverse("invoice:metrics"))
        self.assertNotEqual(response.status_code, 200)

    def test_metrics_page_loads(self):
        self._make_invoice(status=InvoiceStatus.PENDING)
        self._make_invoice(status=InvoiceStatus.PAID, paid_at=timezone.now())
        self.client.login(username="alice", password="pass")
        response = self.client.get(reverse("invoice:metrics"))
        self.assertEqual(response.status_code, 200)
        totals = response.context["totals"]
        self.assertEqual(totals["total"], 2)
        self.assertEqual(totals["pending"], 1)
        self.assertEqual(totals["paid"], 1)


class BucketQuotaTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user("admin", password="pass", is_staff=True)

    def test_bucket_quota_field(self):
        bucket = Bucket.objects.create(
            name="TestBucket",
            slug="testbucket",
            owner=self.user,
            storage_quota_mb=500,
        )
        self.assertEqual(bucket.storage_quota_mb, 500)

    def test_bucket_quota_nullable(self):
        bucket = Bucket.objects.create(
            name="UnlimitedBucket",
            slug="unlimitedbucket",
            owner=self.user,
            storage_quota_mb=None,
        )
        self.assertIsNone(bucket.storage_quota_mb)


class NoVaultAssetsLinkTest(TestCase):
    """Asserts that vault models contain no FK references to the assets app."""

    VAULT_MODELS = [Bucket, VaultDirectory]

    def test_no_assets_fk_in_vault_models(self):
        from django.db import models as _m
        for Model in self.VAULT_MODELS:
            for field in Model._meta.get_fields():
                if isinstance(field, (_m.ForeignKey, _m.OneToOneField, _m.ManyToManyField)):
                    related = getattr(field, 'related_model', None)
                    if related is None:
                        continue
                    app = related._meta.app_label
                    self.assertNotEqual(
                        app, "assets",
                        msg=f"{Model.__name__}.{field.name} points to assets app model {related.__name__}",
                    )
