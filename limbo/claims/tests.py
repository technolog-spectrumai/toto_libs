"""
Claims app UI tests.
Tests cover: page rendering, search/filter params, graph endpoint, template hygiene.
"""
import glob
import json
import os
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

_SIMPLE_STATIC = "django.contrib.staticfiles.storage.StaticFilesStorage"

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_account(code, account_type="user"):
    from toto.assets.models import LedgerAccount
    acc, _ = LedgerAccount.objects.get_or_create(
        code=code,
        defaults={"name": code, "account_type": account_type, "active": True},
    )
    return acc


def _make_asset(unit_name, reserve_account):
    from toto.assets.models import Asset, LedgerTransaction
    from toto.assets.services.assets import create_asset
    ref = f"test-claims-{unit_name.lower()}"
    if LedgerTransaction.objects.filter(reference=ref).exists():
        return Asset.objects.get(unit_name=unit_name)
    return create_asset(
        name=unit_name, unit_name=unit_name,
        total_supply=Decimal("100000"), decimals=2,
        reserve_account=reserve_account, reference=ref,
    )


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

@override_settings(
    STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
              "staticfiles": {"BACKEND": _SIMPLE_STATIC}},
)
class ClaimsTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="claims-tester", password="x")
        cls.reserve = _make_account("test-claims-reserve", "reserve")
        cls.alice   = _make_account("test-claims-alice")
        cls.bob     = _make_account("test-claims-bob")
        cls.asset   = _make_asset("TCLM", cls.reserve)

    def setUp(self):
        self.client.force_login(self.user)
        patcher = patch("toto.ui.page.PageProcessor._get_config", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)


# ---------------------------------------------------------------------------
# 1. Overview / Dashboard
# ---------------------------------------------------------------------------

class ClaimsDashboardTests(ClaimsTestBase):
    def test_overview_renders(self):
        r = self.client.get(reverse("claims:overview"))
        self.assertEqual(r.status_code, 200)

    def test_overview_contains_section_labels(self):
        r = self.client.get(reverse("claims:overview"))
        content = r.content.decode()
        for label in ("Entitlements", "Obligations", "Schedules", "Conditions",
                      "Allocations", "Events"):
            self.assertIn(label, content)

    def test_overview_no_ast_teal(self):
        r = self.client.get(reverse("claims:overview"))
        content = r.content.decode()
        self.assertNotIn("TEAL", content)
        self.assertNotIn(" AST ", content)


# ---------------------------------------------------------------------------
# 2. List pages — empty state + search/filter don't crash
# ---------------------------------------------------------------------------

class ClaimsListPagesTests(ClaimsTestBase):
    def _get(self, name, params=None):
        url = reverse(f"claims:{name}")
        return self.client.get(url, params or {})

    def test_entitlement_list(self):
        self.assertEqual(self._get("entitlement_list").status_code, 200)

    def test_entitlement_list_search(self):
        self.assertEqual(self._get("entitlement_list", {"q": "xyz"}).status_code, 200)

    def test_entitlement_list_status_filter(self):
        self.assertEqual(self._get("entitlement_list", {"status": "active"}).status_code, 200)

    def test_entitlement_list_kind_filter(self):
        self.assertEqual(self._get("entitlement_list", {"kind": "service_access"}).status_code, 200)

    def test_obligation_list(self):
        self.assertEqual(self._get("obligation_list").status_code, 200)

    def test_obligation_list_search(self):
        self.assertEqual(self._get("obligation_list", {"q": "CLM"}).status_code, 200)

    def test_obligation_list_status_filter(self):
        self.assertEqual(self._get("obligation_list", {"status": "pending"}).status_code, 200)

    def test_schedule_list(self):
        self.assertEqual(self._get("schedule_list").status_code, 200)

    def test_schedule_list_search(self):
        self.assertEqual(self._get("schedule_list", {"q": "billing"}).status_code, 200)

    def test_schedule_list_kind_filter(self):
        self.assertEqual(self._get("schedule_list", {"kind": "billing"}).status_code, 200)

    def test_condition_list(self):
        self.assertEqual(self._get("condition_list").status_code, 200)

    def test_condition_list_search(self):
        self.assertEqual(self._get("condition_list", {"q": "KYC"}).status_code, 200)

    def test_allocation_list(self):
        self.assertEqual(self._get("allocation_list").status_code, 200)

    def test_allocation_list_search(self):
        self.assertEqual(self._get("allocation_list", {"q": "TCLM"}).status_code, 200)

    def test_event_list(self):
        self.assertEqual(self._get("event_list").status_code, 200)

    def test_event_list_kind_filter(self):
        self.assertEqual(self._get("event_list", {"kind": "created"}).status_code, 200)


# ---------------------------------------------------------------------------
# 3. Detail pages
# ---------------------------------------------------------------------------

class ClaimsDetailPagesTests(ClaimsTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        now = timezone.now()

        from toto.claims.models import (
            Allocation, AllocationKind, AllocationStatus,
            Condition, ConditionKind, ConditionStatus,
            ContractEvent, ContractEventKind,
            Entitlement, EntitlementKind, EntitlementStatus,
            Schedule, ScheduleKind, ScheduleStatus,
        )
        from toto.assets.models import Obligation, ObligationStatus

        cls.entitlement = Entitlement.objects.create(
            holder_account=cls.alice,
            resource_label="Test API Access",
            kind=EntitlementKind.SERVICE_ACCESS,
            status=EntitlementStatus.ACTIVE,
            source_type="test", source_id="ent-1",
            starts_at=now,
        )
        cls.schedule = Schedule.objects.create(
            name="Test Monthly",
            kind=ScheduleKind.BILLING,
            status=ScheduleStatus.ACTIVE,
            frequency="monthly",
            starts_at=now,
            source_type="test", source_id="sched-1",
        )
        cls.condition = Condition.objects.create(
            name="Test Condition",
            kind=ConditionKind.TIME,
            status=ConditionStatus.PENDING,
            description="Test condition desc.",
            expression={"type": "now_before", "datetime": "2030-01-01T00:00:00Z"},
            source_type="test", source_id="cond-1",
        )
        cls.allocation = Allocation.objects.create(
            asset=cls.asset,
            amount_base_units=10_000,
            kind=AllocationKind.RESERVE,
            status=AllocationStatus.ACTIVE,
            holder_account=cls.alice,
            source_type="test", source_id="alloc-1",
            starts_at=now,
        )
        cls.obligation = Obligation.objects.create(
            reference="TEST-OBL-001",
            debtor_account=cls.alice,
            creditor_account=cls.bob,
            asset=cls.asset,
            amount_base_units=5_000,
            due_at=now,
            status=ObligationStatus.PENDING,
        )
        cls.event = ContractEvent.objects.create(
            kind=ContractEventKind.CREATED,
            title="Test event",
            source_type="test", source_id="evt-1",
        )

    def test_entitlement_detail(self):
        url = reverse("claims:entitlement_detail", args=[self.entitlement.uuid])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Test API Access")

    def test_entitlement_detail_contains_graph(self):
        url = reverse("claims:entitlement_detail", args=[self.entitlement.uuid])
        r = self.client.get(url)
        self.assertContains(r, "cy-lifecycle")
        self.assertContains(r, "oya-theme-colors")

    def test_schedule_detail(self):
        url = reverse("claims:schedule_detail", args=[self.schedule.uuid])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Test Monthly")

    def test_schedule_detail_contains_graph(self):
        url = reverse("claims:schedule_detail", args=[self.schedule.uuid])
        r = self.client.get(url)
        self.assertContains(r, "cy-lifecycle")

    def test_condition_detail(self):
        url = reverse("claims:condition_detail", args=[self.condition.uuid])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Test Condition")

    def test_condition_detail_expression_shown(self):
        url = reverse("claims:condition_detail", args=[self.condition.uuid])
        r = self.client.get(url)
        self.assertContains(r, "now_before")

    def test_allocation_detail(self):
        url = reverse("claims:allocation_detail", args=[self.allocation.uuid])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "10000")

    def test_allocation_detail_contains_graph(self):
        url = reverse("claims:allocation_detail", args=[self.allocation.uuid])
        r = self.client.get(url)
        self.assertContains(r, "cy-lifecycle")

    def test_obligation_detail(self):
        url = reverse("claims:obligation_detail", args=[self.obligation.pk])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "TEST-OBL-001")

    def test_obligation_detail_contains_graph(self):
        url = reverse("claims:obligation_detail", args=[self.obligation.pk])
        r = self.client.get(url)
        self.assertContains(r, "cy-lifecycle")

    def test_event_detail(self):
        url = reverse("claims:event_detail", args=[self.event.uuid])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Test event")

    def test_event_detail_contains_graph(self):
        url = reverse("claims:event_detail", args=[self.event.uuid])
        r = self.client.get(url)
        self.assertContains(r, "cy-lifecycle")

    def test_detail_404_for_unknown_uuid(self):
        import uuid
        url = reverse("claims:entitlement_detail", args=[uuid.uuid4()])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 404)

    def test_obligation_404_for_unknown_pk(self):
        url = reverse("claims:obligation_detail", args=[999999])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 404)


# ---------------------------------------------------------------------------
# 4. Graph endpoint
# ---------------------------------------------------------------------------

class ClaimsGraphEndpointTests(ClaimsTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        now = timezone.now()

        from toto.claims.models import (
            Allocation, AllocationKind, AllocationStatus,
            Condition, ConditionKind, ConditionStatus,
            ContractEvent, ContractEventKind,
            Entitlement, EntitlementKind, EntitlementStatus,
            Schedule, ScheduleKind, ScheduleStatus,
        )
        from toto.assets.models import Obligation, ObligationStatus

        cls.entitlement = Entitlement.objects.create(
            holder_account=cls.alice, resource_label="Graph Test Entitlement",
            kind=EntitlementKind.SERVICE_ACCESS, status=EntitlementStatus.ACTIVE,
            source_type="test_graph", source_id="g-ent",
            starts_at=now,
        )
        cls.schedule = Schedule.objects.create(
            name="Graph Test Schedule", kind=ScheduleKind.BILLING,
            status=ScheduleStatus.ACTIVE, frequency="monthly", starts_at=now,
            source_type="test_graph", source_id="g-sched",
        )
        cls.condition = Condition.objects.create(
            name="Graph Test Condition", kind=ConditionKind.TIME,
            status=ConditionStatus.PENDING, source_type="test_graph", source_id="g-cond",
        )
        cls.allocation = Allocation.objects.create(
            asset=cls.asset, amount_base_units=1000,
            kind=AllocationKind.RESERVE, status=AllocationStatus.ACTIVE,
            holder_account=cls.alice, source_type="test_graph", source_id="g-alloc",
            starts_at=now,
        )
        cls.obligation = Obligation.objects.create(
            reference="GRAPH-OBL-001", debtor_account=cls.alice, creditor_account=cls.bob,
            asset=cls.asset, amount_base_units=500, due_at=now,
            status=ObligationStatus.PENDING,
        )
        cls.event = ContractEvent.objects.create(
            kind=ContractEventKind.CREATED, title="Graph Test Event",
            source_type="test_graph", source_id="g-evt",
        )

    def _graph(self, **params):
        return self.client.get(reverse("claims:lifecycle_graph_json"), params)

    def _assert_graph_ok(self, r):
        self.assertEqual(r.status_code, 200)
        data = json.loads(r.content)
        self.assertIn("nodes", data)
        self.assertIn("edges", data)
        return data

    def test_entitlement_graph(self):
        r = self._graph(model="entitlement", id=str(self.entitlement.uuid))
        data = self._assert_graph_ok(r)
        self.assertTrue(len(data["nodes"]) >= 1)

    def test_entitlement_graph_includes_account(self):
        r = self._graph(model="entitlement", id=str(self.entitlement.uuid), include_accounts="1")
        data = self._assert_graph_ok(r)
        kinds = [n["data"]["kind"] for n in data["nodes"]]
        self.assertIn("account", kinds)

    def test_entitlement_graph_exclude_accounts(self):
        r = self._graph(model="entitlement", id=str(self.entitlement.uuid), include_accounts="0")
        data = self._assert_graph_ok(r)
        kinds = [n["data"]["kind"] for n in data["nodes"]]
        self.assertNotIn("account", kinds)

    def test_obligation_graph(self):
        r = self._graph(model="obligation", id=str(self.obligation.pk))
        data = self._assert_graph_ok(r)
        self.assertTrue(len(data["nodes"]) >= 1)

    def test_schedule_graph(self):
        r = self._graph(model="schedule", id=str(self.schedule.uuid))
        self._assert_graph_ok(r)

    def test_condition_graph(self):
        r = self._graph(model="condition", id=str(self.condition.uuid))
        self._assert_graph_ok(r)

    def test_allocation_graph(self):
        r = self._graph(model="allocation", id=str(self.allocation.uuid))
        self._assert_graph_ok(r)

    def test_event_graph(self):
        r = self._graph(model="event", id=str(self.event.uuid))
        self._assert_graph_ok(r)

    def test_graph_unknown_model_returns_empty(self):
        r = self._graph(model="bogus", id="1")
        data = self._assert_graph_ok(r)
        self.assertEqual(data["nodes"], [])

    def test_graph_missing_id_returns_empty(self):
        r = self._graph(model="entitlement", id="not-a-uuid")
        self.assertEqual(r.status_code, 200)

    def test_graph_include_events_param(self):
        r = self._graph(model="entitlement", id=str(self.entitlement.uuid), include_events="1")
        self._assert_graph_ok(r)

    def test_graph_exclude_events_param(self):
        r = self._graph(model="entitlement", id=str(self.entitlement.uuid), include_events="0")
        self._assert_graph_ok(r)


# ---------------------------------------------------------------------------
# 5. Template hygiene — no forbidden strings
# ---------------------------------------------------------------------------

class ClaimsTemplateHygieneTests(TestCase):
    TEMPLATES_DIR = os.path.join(
        os.path.dirname(__file__), "templates", "claims"
    )

    def _all_templates(self):
        return glob.glob(os.path.join(self.TEMPLATES_DIR, "**", "*.html"), recursive=True)

    def test_no_template_contains_teal(self):
        for path in self._all_templates():
            with open(path) as f:
                content = f.read()
            self.assertNotIn("TEAL", content, f"TEAL found in {path}")

    def test_no_template_contains_ast_word(self):
        for path in self._all_templates():
            with open(path) as f:
                content = f.read()
            # Guard word-boundary: reject " AST " or ">AST<" but allow "last", "cast" etc.
            import re
            self.assertFalse(
                re.search(r"\bAST\b", content),
                f"'AST' found in {path}"
            )

    def test_no_dry_run_buttons(self):
        for path in self._all_templates():
            with open(path) as f:
                content = f.read()
            self.assertNotIn("dry-run", content.lower(), f"'dry-run' found in {path}")
            self.assertNotIn("dry run", content.lower(), f"'dry run' found in {path}")
            self.assertNotIn("test-contract", content.lower(), f"'test-contract' found in {path}")

    def test_detail_templates_include_graph(self):
        detail_templates = [
            "entitlement_detail.html",
            "schedule_detail.html",
            "condition_detail.html",
            "allocation_detail.html",
            "obligation_detail.html",
            "event_detail.html",
        ]
        for name in detail_templates:
            path = os.path.join(self.TEMPLATES_DIR, name)
            with open(path) as f:
                content = f.read()
            self.assertIn("_lifecycle_graph.html", content, f"{name} missing lifecycle graph include")

    def test_graph_partial_contains_oya_theme_colors(self):
        path = os.path.join(self.TEMPLATES_DIR, "_lifecycle_graph.html")
        with open(path) as f:
            content = f.read()
        self.assertIn("oya-theme-colors", content)
        self.assertIn("cy-lifecycle", content)
