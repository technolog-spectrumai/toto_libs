"""
Tests for the usage-statement billing handoff:
  - Metering YAML helpers
  - Source app exporters (vault, ravioli, steven)
  - Tariff preview / pricing / apply services
  - Views (apply page, applications list/detail)
  - Architecture constraints (import boundaries)
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import TestCase, RequestFactory
from django.urls import reverse

from toto.assets.models import (
    AccountType,
    Asset,
    AssetHolding,
    LedgerAccount,
    to_base_units,
)
from toto.metering.usage_statement import (
    build_usage_statement,
    dump_usage_statement_yaml,
    load_usage_statement_yaml,
    validate_usage_statement,
)
from toto.tariffs.models import (
    BillingMetric,
    BillingUnit,
    Tariff,
    TariffApplication,
    TariffApplicationLine,
    TariffApplicationStatus,
    TariffItem,
    TariffStatus,
)
from toto.tariffs.usage_statement_services import (
    apply_tariff_to_usage_statement,
    preview_usage_statement,
    price_usage_statement,
    read_usage_statement_from_vault_file,
)
from toto.invoice.models import Invoice, InvoiceLine
from toto.vault.models import VaultFile

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def make_asset(unit_name="TOKEN", decimals=6):
    return Asset.objects.create(
        name=unit_name,
        unit_name=unit_name,
        decimals=decimals,
        total_supply_base_units=10 ** 15,
        active=True,
    )


def make_account(code, account_type=AccountType.USER):
    return LedgerAccount.objects.create(
        code=code, name=code, account_type=account_type, active=True,
    )


def make_tariff(code="TEST-TARIFF"):
    return Tariff.objects.create(
        name=code,
        code=code,
        status=TariffStatus.ACTIVE,
    )


def make_metric(code):
    return BillingMetric.objects.create(code=code, label=code, active=True)


def make_tariff_item(tariff, metric_code, asset, receiving_account, price_per_unit=1, unit_quantity=1):
    metric = BillingMetric.objects.get_or_create(code=metric_code, defaults={"label": metric_code, "active": True})[0]
    return TariffItem.objects.create(
        tariff=tariff,
        metric=metric,
        name=f"{metric_code} charge",
        charged_asset=asset,
        price_per_unit_display=Decimal(str(price_per_unit)),
        price_per_unit_base_units=to_base_units(Decimal(str(price_per_unit)), asset.decimals),
        unit_quantity=Decimal(str(unit_quantity)),
        receiving_account=receiving_account,
        active=True,
    )


def make_vault_file(owner, yaml_text: str) -> VaultFile:
    vf = VaultFile(owner=owner, title="test-usage.yaml", file_type="yaml")
    vf.file.save("test-usage.yaml", ContentFile(yaml_text.encode()), save=False)
    vf.save()
    return vf


def minimal_statement(**overrides) -> dict:
    base = {
        "kind": "usage_statement",
        "version": 1,
        "statement_id": str(uuid.uuid4()),
        "source_app": "vault",
        "source_label": "Vault Storage",
        "exported_at": "2025-01-01T00:00:00+00:00",
        "period": {
            "start": "2025-01-01T00:00:00+00:00",
            "end": "2025-01-31T00:00:00+00:00",
        },
        "subject": {
            "key": "auth.User:1",
            "type": "auth.User",
            "id": "1",
            "label": "testuser",
        },
        "lines": [
            {
                "line_id": "line-1",
                "metric_code": "storage.request",
                "quantity": "10",
                "unit": "request",
                "occurred_at": "2025-01-15T12:00:00+00:00",
            }
        ],
    }
    base.update(overrides)
    return base


# ===========================================================================
# 1. Metering YAML helpers
# ===========================================================================

class TestBuildUsageStatement(TestCase):

    def test_build_returns_dict_with_correct_structure(self):
        data = build_usage_statement(
            source_app="vault",
            exported_at="2025-01-01T00:00:00+00:00",
            period_start="2025-01-01T00:00:00+00:00",
            period_end="2025-02-01T00:00:00+00:00",
            subject_key="auth.User:42",
            subject_type="auth.User",
            subject_id="42",
            lines=[{
                "line_id": "l1",
                "metric_code": "storage.request",
                "quantity": "5",
                "unit": "request",
                "occurred_at": "2025-01-10T00:00:00+00:00",
            }],
        )
        self.assertEqual(data["kind"], "usage_statement")
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["subject"]["key"], "auth.User:42")
        self.assertIn("statement_id", data)

    def test_statement_id_auto_generated(self):
        d = build_usage_statement(
            source_app="vault",
            exported_at="2025-01-01T00:00:00+00:00",
            period_start="2025-01-01T00:00:00+00:00",
            period_end="2025-02-01T00:00:00+00:00",
            subject_key="vault.Bucket:1",
            subject_type="vault.Bucket",
            subject_id="1",
            lines=[{
                "line_id": "l1",
                "metric_code": "storage.mb_hour",
                "quantity": "100.0",
                "unit": "mb_hour",
                "occurred_at": "2025-01-10T00:00:00+00:00",
            }],
        )
        self.assertTrue(len(d["statement_id"]) > 0)


class TestValidateUsageStatement(TestCase):

    def test_valid_statement_passes(self):
        data = minimal_statement()
        result = validate_usage_statement(data)
        self.assertEqual(result["kind"], "usage_statement")

    def test_missing_kind_fails(self):
        data = minimal_statement()
        del data["kind"]
        with self.assertRaises(ValidationError) as cm:
            validate_usage_statement(data)
        self.assertIn("kind", cm.exception.message_dict)

    def test_wrong_version_fails(self):
        data = minimal_statement()
        data["version"] = 2
        with self.assertRaises(ValidationError) as cm:
            validate_usage_statement(data)
        self.assertIn("version", cm.exception.message_dict)

    def test_missing_source_app_fails(self):
        data = minimal_statement()
        data["source_app"] = ""
        with self.assertRaises(ValidationError) as cm:
            validate_usage_statement(data)
        self.assertIn("source_app", cm.exception.message_dict)

    def test_period_end_before_start_fails(self):
        data = minimal_statement()
        data["period"]["end"] = "2024-12-01T00:00:00+00:00"
        with self.assertRaises(ValidationError) as cm:
            validate_usage_statement(data)
        self.assertIn("period.end", cm.exception.message_dict)

    def test_missing_subject_key_fails(self):
        data = minimal_statement()
        data["subject"]["key"] = ""
        with self.assertRaises(ValidationError) as cm:
            validate_usage_statement(data)
        self.assertIn("subject.key", cm.exception.message_dict)

    def test_empty_lines_fails(self):
        data = minimal_statement()
        data["lines"] = []
        with self.assertRaises(ValidationError) as cm:
            validate_usage_statement(data)
        self.assertIn("lines", cm.exception.message_dict)

    def test_duplicate_line_id_fails(self):
        data = minimal_statement()
        data["lines"].append({
            "line_id": "line-1",  # duplicate
            "metric_code": "storage.mb_hour",
            "quantity": "50",
            "unit": "mb_hour",
            "occurred_at": "2025-01-20T00:00:00+00:00",
        })
        with self.assertRaises(ValidationError):
            validate_usage_statement(data)

    def test_non_positive_quantity_fails(self):
        data = minimal_statement()
        data["lines"][0]["quantity"] = "0"
        with self.assertRaises(ValidationError):
            validate_usage_statement(data)

    def test_negative_quantity_fails(self):
        data = minimal_statement()
        data["lines"][0]["quantity"] = "-5"
        with self.assertRaises(ValidationError):
            validate_usage_statement(data)

    def test_float_quantity_fails(self):
        data = minimal_statement()
        data["lines"][0]["quantity"] = 10.5  # float, not string
        with self.assertRaises(ValidationError):
            validate_usage_statement(data)

    def test_forbidden_key_amount_fails(self):
        data = minimal_statement()
        data["amount"] = "100"
        with self.assertRaises(ValidationError) as cm:
            validate_usage_statement(data)
        errors = str(cm.exception)
        self.assertIn("amount", errors)

    def test_forbidden_key_invoice_fails(self):
        data = minimal_statement()
        data["invoice_id"] = "INV-123"
        with self.assertRaises(ValidationError):
            validate_usage_statement(data)

    def test_forbidden_key_tariff_in_line_fails(self):
        data = minimal_statement()
        data["lines"][0]["tariff"] = "my-tariff"
        with self.assertRaises(ValidationError):
            validate_usage_statement(data)

    def test_forbidden_key_price_fails(self):
        data = minimal_statement()
        data["price"] = "0.01"
        with self.assertRaises(ValidationError):
            validate_usage_statement(data)


class TestDumpLoadRoundtrip(TestCase):

    def test_dump_produces_yaml_string(self):
        data = minimal_statement()
        yaml_text = dump_usage_statement_yaml(data)
        self.assertIsInstance(yaml_text, str)
        self.assertIn("usage_statement", yaml_text)

    def test_load_roundtrip(self):
        data = minimal_statement()
        yaml_text = dump_usage_statement_yaml(data)
        loaded = load_usage_statement_yaml(yaml_text)
        self.assertEqual(loaded["statement_id"], data["statement_id"])
        self.assertEqual(loaded["lines"][0]["line_id"], "line-1")

    def test_load_invalid_yaml_raises(self):
        with self.assertRaises(ValidationError):
            load_usage_statement_yaml("not: valid: yaml: [")

    def test_load_non_dict_yaml_raises(self):
        with self.assertRaises(ValidationError):
            load_usage_statement_yaml("- item1\n- item2\n")

    def test_decimal_serialised_as_string(self):
        data = minimal_statement()
        data["lines"][0]["quantity"] = "123.456789"
        yaml_text = dump_usage_statement_yaml(data)
        loaded = load_usage_statement_yaml(yaml_text)
        self.assertEqual(loaded["lines"][0]["quantity"], "123.456789")


# ===========================================================================
# 2. Vault exporter
# ===========================================================================

class TestVaultUsageExport(TestCase):

    def setUp(self):
        self.user = User.objects.create_user("exporter", password="x")

    def test_build_statement_has_subject_key(self):
        from toto.vault.usage_export import export_usage_statement
        from toto.vault.models import Bucket
        bucket = Bucket.objects.create(name="test-bucket", slug="test-bucket", owner=self.user)
        # Upload a file so usage events exist
        vf = VaultFile.objects.create(
            owner=self.user,
            title="test.txt",
            file_type="text",
            bucket=bucket,
        )
        vf.file.save("test.txt", ContentFile(b"hello"), save=True)

        data = export_usage_statement(
            subject_type="vault.Bucket",
            subject_id=str(bucket.pk),
            period_start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(data["subject"]["key"], f"vault.Bucket:{bucket.pk}")
        self.assertEqual(data["source_app"], "vault")
        self.assertTrue(len(data["lines"]) > 0)
        for line in data["lines"]:
            self.assertIn("metric_code", line)
            self.assertIn("quantity", line)
            self.assertIn("unit", line)

    def test_export_yaml_is_valid(self):
        from toto.vault.usage_export import export_usage_statement_yaml
        from toto.vault.models import Bucket
        bucket = Bucket.objects.create(name="test-bucket-yaml", slug="test-bucket-yaml", owner=self.user)
        vf = VaultFile.objects.create(
            owner=self.user, title="f.txt", file_type="text", bucket=bucket,
        )
        vf.file.save("f.txt", ContentFile(b"data"), save=True)

        yaml_text = export_usage_statement_yaml(
            subject_type="vault.Bucket",
            subject_id=str(bucket.pk),
            period_start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
        loaded = load_usage_statement_yaml(yaml_text)
        self.assertEqual(loaded["kind"], "usage_statement")


# ===========================================================================
# 3. Ravioli exporter (skeleton — just checks the module doesn't import tariffs)
# ===========================================================================

class TestRavioliUsageExportArchitecture(TestCase):

    def test_module_does_not_import_tariffs(self):
        import toto.ravioli.usage_export as mod
        import sys
        # The module itself must not pull in tariffs at import time
        self.assertNotIn("toto.tariffs", sys.modules.get("toto.ravioli.usage_export", mod).__spec__.submodule_search_locations or [])

    def test_module_imports_metering(self):
        import toto.ravioli.usage_export  # must not raise
        import toto.metering.usage_statement  # must be importable


# ===========================================================================
# 4. Steven exporter architecture
# ===========================================================================

class TestStevenUsageExportArchitecture(TestCase):

    def test_module_does_not_import_tariffs(self):
        import toto.steven.usage_export  # must not raise ImportError


# ===========================================================================
# 5. Tariff services: preview
# ===========================================================================

class TestPreviewUsageStatement(TestCase):

    def setUp(self):
        self.user = User.objects.create_user("prevuser", password="x")

    def _make_file(self, data=None):
        if data is None:
            data = minimal_statement()
        yaml_text = dump_usage_statement_yaml(data)
        return make_vault_file(self.user, yaml_text)

    def test_preview_returns_statement_id(self):
        vf = self._make_file()
        result = preview_usage_statement(usage_file=vf)
        self.assertIn("statement_id", result)

    def test_preview_autodetects_source_app(self):
        vf = self._make_file()
        result = preview_usage_statement(usage_file=vf)
        self.assertEqual(result["source_app"], "vault")

    def test_preview_autodetects_subject(self):
        vf = self._make_file()
        result = preview_usage_statement(usage_file=vf)
        self.assertEqual(result["subject"]["key"], "auth.User:1")

    def test_preview_returns_metrics_summary(self):
        vf = self._make_file()
        result = preview_usage_statement(usage_file=vf)
        self.assertEqual(len(result["metrics_summary"]), 1)
        self.assertEqual(result["metrics_summary"][0]["metric_code"], "storage.request")
        self.assertEqual(result["metrics_summary"][0]["total_quantity"], Decimal("10"))

    def test_preview_returns_line_count(self):
        vf = self._make_file()
        result = preview_usage_statement(usage_file=vf)
        self.assertEqual(result["line_count"], 1)

    def test_preview_invalid_yaml_raises(self):
        vf = make_vault_file(self.user, "not valid yaml content ===")
        with self.assertRaises(ValidationError):
            preview_usage_statement(usage_file=vf)


# ===========================================================================
# 6. Tariff services: price
# ===========================================================================

class TestPriceUsageStatement(TestCase):

    def setUp(self):
        self.user = User.objects.create_user("priceuser", password="x")
        self.asset = make_asset("CREDIT", decimals=2)
        self.recv_account = make_account("recv-account", AccountType.SYSTEM)
        self.tariff = make_tariff("PRICE-TARIFF")
        make_tariff_item(self.tariff, "storage.request", self.asset, self.recv_account, price_per_unit="0.01")

    def _make_file(self, data=None):
        if data is None:
            data = minimal_statement()
        yaml_text = dump_usage_statement_yaml(data)
        return make_vault_file(self.user, yaml_text)

    def test_prices_each_line_with_matching_tariff_item(self):
        vf = self._make_file()
        result = price_usage_statement(usage_file=vf, tariff=self.tariff)
        self.assertFalse(result["has_errors"])
        self.assertEqual(len(result["priced_lines"]), 1)
        line = result["priced_lines"][0]
        self.assertEqual(line["metric_code"], "storage.request")
        self.assertTrue(len(line["charges"]) > 0)

    def test_missing_tariff_item_produces_clean_error(self):
        data = minimal_statement()
        data["lines"][0]["metric_code"] = "unknown.metric"
        yaml_text = dump_usage_statement_yaml(data)
        vf = make_vault_file(self.user, yaml_text)
        result = price_usage_statement(usage_file=vf, tariff=self.tariff)
        self.assertTrue(result["has_errors"])
        self.assertEqual(result["errors"][0]["metric_code"], "unknown.metric")

    def test_does_not_create_invoice_during_preview(self):
        vf = self._make_file()
        before = Invoice.objects.count()
        price_usage_statement(usage_file=vf, tariff=self.tariff)
        self.assertEqual(Invoice.objects.count(), before)

    def test_totals_by_asset_populated(self):
        vf = self._make_file()
        result = price_usage_statement(usage_file=vf, tariff=self.tariff)
        self.assertTrue(len(result["totals_by_asset"]) > 0)
        self.assertIn("asset", result["totals_by_asset"][0])


# ===========================================================================
# 7. Tariff services: apply
# ===========================================================================

class TestApplyTariffToUsageStatement(TestCase):

    def setUp(self):
        self.user = User.objects.create_user("applyuser", password="x")
        self.issued_to = User.objects.create_user("billme", password="x")
        self.asset = make_asset("COIN", decimals=4)
        self.recv_account = make_account("recv-coin", AccountType.SYSTEM)
        self.tariff = make_tariff("APPLY-TARIFF")
        make_tariff_item(self.tariff, "storage.request", self.asset, self.recv_account, price_per_unit="0.001")

    def _make_file(self, data=None):
        if data is None:
            data = minimal_statement()
        yaml_text = dump_usage_statement_yaml(data)
        return make_vault_file(self.user, yaml_text)

    def test_creates_tariff_application(self):
        vf = self._make_file()
        app, invoice = apply_tariff_to_usage_statement(
            usage_file=vf, tariff=self.tariff, issued_to=self.issued_to,
        )
        self.assertIsInstance(app, TariffApplication)
        self.assertEqual(app.status, TariffApplicationStatus.INVOICED)

    def test_creates_invoice(self):
        vf = self._make_file()
        app, invoice = apply_tariff_to_usage_statement(
            usage_file=vf, tariff=self.tariff, issued_to=self.issued_to,
        )
        self.assertIsInstance(invoice, Invoice)
        self.assertEqual(invoice.issued_to, self.issued_to)

    def test_creates_invoice_lines(self):
        vf = self._make_file()
        app, invoice = apply_tariff_to_usage_statement(
            usage_file=vf, tariff=self.tariff, issued_to=self.issued_to,
        )
        self.assertEqual(invoice.lines.count(), 1)

    def test_creates_application_lines(self):
        vf = self._make_file()
        app, invoice = apply_tariff_to_usage_statement(
            usage_file=vf, tariff=self.tariff, issued_to=self.issued_to,
        )
        self.assertEqual(app.lines.count(), 1)

    def test_application_stores_invoice_source(self):
        vf = self._make_file()
        app, invoice = apply_tariff_to_usage_statement(
            usage_file=vf, tariff=self.tariff, issued_to=self.issued_to,
        )
        self.assertEqual(app.invoice_source_type, "invoice.Invoice")
        self.assertEqual(app.invoice_source_id, str(invoice.pk))

    def test_detects_source_app(self):
        vf = self._make_file()
        app, invoice = apply_tariff_to_usage_statement(
            usage_file=vf, tariff=self.tariff, issued_to=self.issued_to,
        )
        self.assertEqual(app.detected_source_app, "vault")

    def test_detects_subject_key(self):
        vf = self._make_file()
        app, invoice = apply_tariff_to_usage_statement(
            usage_file=vf, tariff=self.tariff, issued_to=self.issued_to,
        )
        self.assertEqual(app.detected_subject_key, "auth.User:1")

    def test_override_fields_stored(self):
        vf = self._make_file()
        app, invoice = apply_tariff_to_usage_statement(
            usage_file=vf, tariff=self.tariff, issued_to=self.issued_to,
            override_source_app="my-app",
            override_subject_label="My Subject",
        )
        self.assertEqual(app.override_source_app, "my-app")
        self.assertEqual(app.override_subject_label, "My Subject")
        self.assertEqual(app.effective_source_app, "my-app")
        self.assertEqual(app.effective_subject_label, "My Subject")

    def test_rejects_duplicate_statement_id(self):
        data = minimal_statement()
        vf1 = self._make_file(data)
        apply_tariff_to_usage_statement(usage_file=vf1, tariff=self.tariff, issued_to=self.issued_to)
        vf2 = self._make_file(data)  # same statement_id
        with self.assertRaises(ValidationError) as cm:
            apply_tariff_to_usage_statement(usage_file=vf2, tariff=self.tariff, issued_to=self.issued_to)
        self.assertIn("statement_id", cm.exception.message_dict)

    def test_does_not_create_obligation(self):
        from toto.assets.models import Obligation
        vf = self._make_file()
        before = Obligation.objects.count()
        apply_tariff_to_usage_statement(
            usage_file=vf, tariff=self.tariff, issued_to=self.issued_to,
        )
        self.assertEqual(Obligation.objects.count(), before)

    def test_does_not_create_ledger_transaction(self):
        from toto.assets.models import LedgerTransaction
        vf = self._make_file()
        before = LedgerTransaction.objects.count()
        apply_tariff_to_usage_statement(
            usage_file=vf, tariff=self.tariff, issued_to=self.issued_to,
        )
        self.assertEqual(LedgerTransaction.objects.count(), before)

    def test_does_not_mutate_asset_holding(self):
        vf = self._make_file()
        before = {h.pk: h.balance_base_units for h in AssetHolding.objects.all()}
        apply_tariff_to_usage_statement(
            usage_file=vf, tariff=self.tariff, issued_to=self.issued_to,
        )
        for h in AssetHolding.objects.filter(pk__in=before.keys()):
            self.assertEqual(h.balance_base_units, before[h.pk])

    def test_missing_tariff_item_raises(self):
        data = minimal_statement()
        data["lines"][0]["metric_code"] = "nonexistent.metric"
        yaml_text = dump_usage_statement_yaml(data)
        vf = make_vault_file(self.user, yaml_text)
        with self.assertRaises(ValidationError):
            apply_tariff_to_usage_statement(usage_file=vf, tariff=self.tariff, issued_to=self.issued_to)

    def test_invoice_line_source_links_back(self):
        vf = self._make_file()
        app, invoice = apply_tariff_to_usage_statement(
            usage_file=vf, tariff=self.tariff, issued_to=self.issued_to,
        )
        inv_line = invoice.lines.first()
        self.assertEqual(inv_line.source_type, "tariffs.TariffApplicationLine")


# ===========================================================================
# 8. Views
# ===========================================================================

def make_platform():
    from toto.core.models import Platform
    return Platform.objects.get_or_create(
        site_name="Test Platform",
        defaults={"author": "test", "publication_year": 2025, "active": True},
    )[0]


class TestApplyUsageStatementViews(TestCase):

    def setUp(self):
        make_platform()
        self.user = User.objects.create_superuser("admin", "a@a.com", "password")
        self.client.force_login(self.user)
        self.asset = make_asset("VIEW_TOK", decimals=2)
        self.recv_account = make_account("view-recv", AccountType.SYSTEM)
        self.tariff = make_tariff("VIEW-TARIFF")
        make_tariff_item(self.tariff, "storage.request", self.asset, self.recv_account, price_per_unit="1")

    def test_apply_page_renders(self):
        url = reverse("tariffs:apply_usage_statement")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "APPLY TARIFF YEAH")

    def test_preview_button_shows_formatted_info(self):
        data = minimal_statement()
        yaml_text = dump_usage_statement_yaml(data)
        vf = make_vault_file(self.user, yaml_text)
        url = reverse("tariffs:apply_usage_statement")
        resp = self.client.post(url, {
            "action": "preview",
            "usage_file": vf.pk,
            "tariff": self.tariff.pk,
            "issued_to": self.user.pk,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, data["statement_id"])
        self.assertContains(resp, "storage.request")

    def test_apply_creates_invoice_and_redirects(self):
        data = minimal_statement()
        yaml_text = dump_usage_statement_yaml(data)
        vf = make_vault_file(self.user, yaml_text)
        url = reverse("tariffs:apply_usage_statement")
        before_invoices = Invoice.objects.count()
        resp = self.client.post(url, {
            "action": "apply",
            "usage_file": vf.pk,
            "tariff": self.tariff.pk,
            "issued_to": self.user.pk,
        })
        self.assertEqual(Invoice.objects.count(), before_invoices + 1)
        self.assertRedirects(resp, reverse(
            "tariffs:tariff_application_detail",
            kwargs={"uid": TariffApplication.objects.latest("created_at").uid}
        ))

    def test_application_list_renders(self):
        url = reverse("tariffs:tariff_application_list")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_application_detail_renders(self):
        data = minimal_statement()
        yaml_text = dump_usage_statement_yaml(data)
        vf = make_vault_file(self.user, yaml_text)
        app, invoice = apply_tariff_to_usage_statement(
            usage_file=vf, tariff=self.tariff, issued_to=self.user,
        )
        url = reverse("tariffs:tariff_application_detail", kwargs={"uid": app.uid})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, app.statement_id)
        self.assertContains(resp, "storage.request")


# ===========================================================================
# 9. Architecture constraints
# ===========================================================================

class TestArchitectureConstraints(TestCase):

    def test_metering_usage_statement_does_not_import_tariffs(self):
        import ast
        import pathlib
        # test file: toto/toto/tariffs/tests/test_usage_statement.py
        # 3 parents up = toto/toto/ (the apps dir)
        apps_dir = pathlib.Path(__file__).parent.parent.parent
        src = apps_dir / "metering" / "usage_statement.py"
        tree = ast.parse(src.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                self.assertNotIn("tariffs", module, "metering.usage_statement must not import tariffs")
                self.assertNotIn("assets", module, "metering.usage_statement must not import assets")
                self.assertNotIn("invoice", module, "metering.usage_statement must not import invoice")
                self.assertNotIn("budget", module, "metering.usage_statement must not import budget")
                self.assertNotIn("vault", module, "metering.usage_statement must not import vault")

    def test_vault_usage_export_does_not_import_tariffs(self):
        import ast
        import pathlib
        apps_dir = pathlib.Path(__file__).parent.parent.parent
        src = apps_dir / "vault" / "usage_export.py"
        tree = ast.parse(src.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                self.assertNotIn("tariffs", module, "vault.usage_export must not import tariffs")
                self.assertNotIn("invoice", module, "vault.usage_export must not import invoice")

    def test_usage_statement_services_does_not_post_ledger(self):
        import ast
        import pathlib
        src = pathlib.Path(__file__).parent.parent / "usage_statement_services.py"
        source = src.read_text()
        tree = ast.parse(source)
        # Check no problematic function calls appear in code (not in comments/docstrings)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # Check for post_usage_record calls
                func = node.func
                if isinstance(func, ast.Name):
                    self.assertNotEqual(func.id, "post_usage_record", "services must not call post_usage_record")
        # Check imported names — should not import AssetHolding, Obligation, or LedgerTransaction
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.name)
        self.assertNotIn("AssetHolding", imported_names, "services must not import AssetHolding")
        self.assertNotIn("Obligation", imported_names, "services must not import Obligation")
        self.assertNotIn("LedgerTransaction", imported_names, "services must not import LedgerTransaction")
