"""
Tests for toto.metering.

Covers:
- Model constraints (quantity positive, unit default, idempotency)
- Service behaviour (record_usage, void, quota windows, evaluate_quota)
- Dependency purity (no tariffs/assets/invoice/budget imports)
- View smoke tests
- api_record_usage endpoint
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    EventStatus, QuotaMode, QuotaPeriod,
    UsageEvent, UsageMetric, UsageQuota,
)
from .services import (
    QuotaExceeded, evaluate_quota,
    quota_window, record_usage,
    usage_total_for_quota, void_usage_event,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_metric(code="test.bytes", unit="bytes"):
    return UsageMetric.objects.create(code=code, name=code, default_unit=unit)


def make_quota(metric, code="q1", limit=100, period=QuotaPeriod.LIFETIME,
               mode=QuotaMode.BLOCK, subject_type="", subject_id=""):
    return UsageQuota.objects.create(
        code=code, name=code, metric=metric,
        limit_quantity=Decimal(str(limit)), unit=metric.default_unit,
        period=period, mode=mode,
        subject_type=subject_type, subject_id=subject_id,
        is_active=True,
    )


# ---------------------------------------------------------------------------
# Model: quantity must be positive
# ---------------------------------------------------------------------------

class UsageEventQuantityTest(TestCase):
    def setUp(self):
        self.metric = make_metric()

    def test_positive_quantity_allowed(self):
        event = UsageEvent.objects.create(
            metric=self.metric, quantity=Decimal("1.5"),
        )
        self.assertEqual(event.quantity, Decimal("1.5"))

    def test_zero_quantity_rejected_at_db(self):
        from django.db import IntegrityError
        with self.assertRaises(Exception):
            UsageEvent.objects.create(metric=self.metric, quantity=Decimal("0"))

    def test_negative_quantity_rejected_at_db(self):
        from django.db import IntegrityError
        with self.assertRaises(Exception):
            UsageEvent.objects.create(metric=self.metric, quantity=Decimal("-1"))


# ---------------------------------------------------------------------------
# Model: unit defaults from metric.default_unit
# ---------------------------------------------------------------------------

class UnitDefaultTest(TestCase):
    def test_unit_defaults_from_metric(self):
        metric = make_metric(code="m.default", unit="requests")
        event = UsageEvent.objects.create(metric=metric, quantity=Decimal("1"))
        self.assertEqual(event.unit, "requests")

    def test_explicit_unit_not_overridden(self):
        metric = make_metric(code="m.explicit", unit="bytes")
        event = UsageEvent.objects.create(metric=metric, quantity=Decimal("1"), unit="mb")
        self.assertEqual(event.unit, "mb")


# ---------------------------------------------------------------------------
# Model: idempotency key prevents duplicates
# ---------------------------------------------------------------------------

class IdempotencyKeyTest(TestCase):
    def setUp(self):
        self.metric = make_metric(code="m.idem")

    def test_duplicate_idempotency_key_rejected(self):
        from django.db import IntegrityError
        UsageEvent.objects.create(
            metric=self.metric, quantity=Decimal("1"), idempotency_key="key-abc"
        )
        with self.assertRaises(IntegrityError):
            UsageEvent.objects.create(
                metric=self.metric, quantity=Decimal("2"), idempotency_key="key-abc"
            )

    def test_empty_idempotency_key_not_unique(self):
        UsageEvent.objects.create(metric=self.metric, quantity=Decimal("1"), idempotency_key="")
        UsageEvent.objects.create(metric=self.metric, quantity=Decimal("1"), idempotency_key="")
        self.assertEqual(UsageEvent.objects.filter(idempotency_key="").count(), 2)


# ---------------------------------------------------------------------------
# Service: record_usage
# ---------------------------------------------------------------------------

class RecordUsageTest(TestCase):
    def setUp(self):
        self.metric = make_metric(code="s.req", unit="req")

    def test_creates_event(self):
        event = record_usage(metric_code="s.req", quantity=5, enforce_quota=False)
        self.assertEqual(event.metric.code, "s.req")
        self.assertEqual(event.quantity, Decimal("5"))
        self.assertEqual(event.status, EventStatus.RECORDED)

    def test_unknown_metric_raises(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            record_usage(metric_code="no.exist", quantity=1, enforce_quota=False)

    def test_create_metric_on_demand(self):
        event = record_usage(
            metric_code="new.metric", quantity=1,
            create_metric=True, enforce_quota=False,
        )
        self.assertEqual(event.metric.code, "new.metric")

    def test_idempotency_returns_existing(self):
        e1 = record_usage(
            metric_code="s.req", quantity=3,
            idempotency_key="idem-1", enforce_quota=False,
        )
        e2 = record_usage(
            metric_code="s.req", quantity=99,
            idempotency_key="idem-1", enforce_quota=False,
        )
        self.assertEqual(e1.pk, e2.pk)
        self.assertEqual(UsageEvent.objects.filter(idempotency_key="idem-1").count(), 1)


# ---------------------------------------------------------------------------
# Service: void_usage_event
# ---------------------------------------------------------------------------

class VoidUsageEventTest(TestCase):
    def test_void_sets_status(self):
        metric = make_metric(code="v.metric")
        event = UsageEvent.objects.create(metric=metric, quantity=Decimal("1"))
        void_usage_event(event, reason="test void")
        event.refresh_from_db()
        self.assertEqual(event.status, EventStatus.VOIDED)
        self.assertEqual(event.metadata["void_reason"], "test void")

    def test_void_does_not_delete(self):
        metric = make_metric(code="v.del")
        event = UsageEvent.objects.create(metric=metric, quantity=Decimal("1"))
        pk = event.pk
        void_usage_event(event)
        self.assertTrue(UsageEvent.objects.filter(pk=pk).exists())


# ---------------------------------------------------------------------------
# Service: quota_window
# ---------------------------------------------------------------------------

class QuotaWindowTest(TestCase):
    def _quota(self, period, rolling=None):
        metric = make_metric(code=f"qw.{period}")
        return UsageQuota(
            metric=metric, limit_quantity=100, period=period,
            rolling_seconds=rolling, unit="",
        )

    def test_lifetime_window(self):
        q = self._quota(QuotaPeriod.LIFETIME)
        start, end = quota_window(q)
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_daily_window(self):
        import datetime
        at = timezone.now().replace(hour=15, minute=30)
        q = self._quota(QuotaPeriod.DAILY)
        start, end = quota_window(q, at)
        self.assertEqual(start.hour, 0)
        self.assertEqual((end - start).days, 1)

    def test_monthly_window(self):
        at = timezone.now().replace(day=15)
        q = self._quota(QuotaPeriod.MONTHLY)
        start, end = quota_window(q, at)
        self.assertEqual(start.day, 1)
        self.assertGreater(end, start)

    def test_rolling_window(self):
        at = timezone.now()
        q = self._quota(QuotaPeriod.ROLLING, rolling=3600)
        start, end = quota_window(q, at)
        from datetime import timedelta
        self.assertAlmostEqual((at - start).total_seconds(), 3600, delta=1)
        self.assertEqual(end, at)


# ---------------------------------------------------------------------------
# Service: evaluate_quota — global vs subject-specific
# ---------------------------------------------------------------------------

class EvaluateQuotaTest(TestCase):
    def setUp(self):
        self.metric = make_metric(code="eq.metric")

    def test_global_quota_applies_to_all_subjects(self):
        make_quota(self.metric, code="global-q", limit=10,
                   period=QuotaPeriod.LIFETIME, mode=QuotaMode.BLOCK)
        # Record 9 units for subject A
        record_usage(metric_code="eq.metric", quantity=9,
                     subject_type="user", subject_id="A", enforce_quota=False)
        # Subject B asking for 2 should be blocked (global used=9, +2 > 10)
        decision = evaluate_quota(
            self.metric, Decimal("2"), subject_type="user", subject_id="B"
        )
        self.assertFalse(decision.allowed)

    def test_subject_quota_applies_only_to_exact_subject(self):
        make_quota(self.metric, code="subj-q", limit=5,
                   period=QuotaPeriod.LIFETIME, mode=QuotaMode.BLOCK,
                   subject_type="user", subject_id="A")
        record_usage(metric_code="eq.metric", quantity=4,
                     subject_type="user", subject_id="A", enforce_quota=False)
        # Subject A: 4+2 > 5, should block
        decision_a = evaluate_quota(
            self.metric, Decimal("2"), subject_type="user", subject_id="A"
        )
        self.assertFalse(decision_a.allowed)
        # Subject B: no quota, should allow
        decision_b = evaluate_quota(
            self.metric, Decimal("2"), subject_type="user", subject_id="B"
        )
        self.assertTrue(decision_b.allowed)

    def test_block_quota_raises_quota_exceeded(self):
        make_quota(self.metric, code="block-q", limit=1,
                   period=QuotaPeriod.LIFETIME, mode=QuotaMode.BLOCK)
        record_usage(metric_code="eq.metric", quantity=1, enforce_quota=False)
        with self.assertRaises(QuotaExceeded):
            record_usage(metric_code="eq.metric", quantity=1, enforce_quota=True)
        # No new event created
        self.assertEqual(
            UsageEvent.objects.filter(status=EventStatus.RECORDED).count(), 1
        )

    def test_warn_quota_allows_event(self):
        make_quota(self.metric, code="warn-q", limit=1,
                   period=QuotaPeriod.LIFETIME, mode=QuotaMode.WARN)
        record_usage(metric_code="eq.metric", quantity=1, enforce_quota=False)
        event = record_usage(metric_code="eq.metric", quantity=1, enforce_quota=True)
        self.assertIsNotNone(event.pk)
        self.assertEqual(event.status, EventStatus.RECORDED)

    def test_track_quota_never_blocks(self):
        make_quota(self.metric, code="track-q", limit=1,
                   period=QuotaPeriod.LIFETIME, mode=QuotaMode.TRACK)
        record_usage(metric_code="eq.metric", quantity=9999, enforce_quota=True)
        self.assertEqual(UsageEvent.objects.count(), 1)

    def test_monthly_quota_window_isolates(self):
        make_quota(self.metric, code="month-q", limit=5,
                   period=QuotaPeriod.MONTHLY, mode=QuotaMode.BLOCK)
        import datetime
        last_month = (timezone.now() - datetime.timedelta(days=35))
        record_usage(metric_code="eq.metric", quantity=5,
                     occurred_at=last_month, enforce_quota=False)
        # Current month has 0 usage, so 5 is fine
        event = record_usage(metric_code="eq.metric", quantity=5, enforce_quota=True)
        self.assertEqual(event.status, EventStatus.RECORDED)


# ---------------------------------------------------------------------------
# Import purity
# ---------------------------------------------------------------------------

class MeteringImportPurityTest(TestCase):
    def test_services_has_no_forbidden_imports(self):
        import ast, os
        forbidden = {"tariffs", "assets", "invoice", "budget", "vault", "vod", "ravioli", "steven"}
        for fname in ("services.py", "models.py", "views.py", "forms.py", "admin.py"):
            path = os.path.join(os.path.dirname(__file__), fname)
            with open(path) as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in getattr(node, "names", []):
                        self.assertFalse(
                            any(f in alias.name for f in forbidden),
                            f"{fname}: forbidden import '{alias.name}'",
                        )
                    if isinstance(node, ast.ImportFrom) and node.module:
                        self.assertFalse(
                            any(f in node.module for f in forbidden),
                            f"{fname}: forbidden import from '{node.module}'",
                        )


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class MeteringViewsTest(TestCase):
    def setUp(self):
        from toto.core.models import Platform
        Platform.objects.create(
            site_name="Test", author="test", publication_year=2026, active=True
        )
        self.user = User.objects.create_user(username="meter_user", password="pass")
        self.client = Client()
        self.client.force_login(self.user)
        self.metric = make_metric(code="v.req")

    def test_dashboard_renders(self):
        r = self.client.get(reverse("metering:dashboard"))
        self.assertEqual(r.status_code, 200)

    def test_metric_list_renders(self):
        r = self.client.get(reverse("metering:metric_list"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "v.req")

    def test_usage_list_renders(self):
        UsageEvent.objects.create(metric=self.metric, quantity=Decimal("1"))
        r = self.client.get(reverse("metering:usage_list"))
        self.assertEqual(r.status_code, 200)

    def test_quota_list_renders(self):
        make_quota(self.metric, code="vtest-q")
        r = self.client.get(reverse("metering:quota_list"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "vtest-q")

    def test_usage_detail_renders(self):
        event = UsageEvent.objects.create(metric=self.metric, quantity=Decimal("1"))
        r = self.client.get(reverse("metering:usage_detail", kwargs={"uid": event.uid}))
        self.assertEqual(r.status_code, 200)

    def test_anonymous_redirected(self):
        c = Client()
        r = c.get(reverse("metering:dashboard"))
        self.assertEqual(r.status_code, 302)


# ---------------------------------------------------------------------------
# API: api_record_usage
# ---------------------------------------------------------------------------

class ApiRecordUsageTest(TestCase):
    def setUp(self):
        make_metric(code="api.req", unit="req")
        self.url = reverse("metering:api_record_usage")

    def test_creates_event(self):
        import json
        r = self.client.post(
            self.url,
            data=json.dumps({"metric_code": "api.req", "quantity": 5}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual(body["metric"], "api.req")
        self.assertEqual(body["status"], "recorded")

    def test_missing_metric_code_returns_400(self):
        import json
        r = self.client.post(
            self.url,
            data=json.dumps({"quantity": 5}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)

    def test_unknown_metric_returns_400(self):
        import json
        r = self.client.post(
            self.url,
            data=json.dumps({"metric_code": "no.exist", "quantity": 1}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)

    def test_quota_exceeded_returns_429(self):
        import json
        metric = UsageMetric.objects.get(code="api.req")
        make_quota(metric, code="api-q", limit=1,
                   period=QuotaPeriod.LIFETIME, mode=QuotaMode.BLOCK)
        UsageEvent.objects.create(metric=metric, quantity=Decimal("1"))
        r = self.client.post(
            self.url,
            data=json.dumps({"metric_code": "api.req", "quantity": 1}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()["error"], "quota_exceeded")
