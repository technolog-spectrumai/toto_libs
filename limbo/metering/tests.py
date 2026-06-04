"""
Tests for toto.metering primitives, services, and quota helpers.

Temporary concrete models (SampleUsageEvent, SampleUsageQuota) are defined
inline so metering itself never has production concrete tables.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone


# ---------------------------------------------------------------------------
# Temporary concrete models for testing
# ---------------------------------------------------------------------------

from toto.metering.models import AbstractUsageEvent, AbstractUsageQuota


class SampleUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        app_label = "metering"


class SampleUsageQuota(AbstractUsageQuota):
    class Meta(AbstractUsageQuota.Meta):
        app_label = "metering"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event(**kwargs):
    kwargs.setdefault("metric_code", "test.metric")
    kwargs.setdefault("quantity", Decimal("1"))
    return SampleUsageEvent(**kwargs)


def _make_quota(code="q1", limit=100, period="lifetime", mode="block",
                subject_type="", subject_id=""):
    return SampleUsageQuota.objects.create(
        code=code, name=code,
        metric_code="test.metric",
        limit_quantity=Decimal(str(limit)),
        unit="unit",
        period=period,
        mode=mode,
        subject_type=subject_type,
        subject_id=subject_id,
        is_active=True,
    )


# ===========================================================================
# Architecture constraints
# ===========================================================================

class MeteringArchitectureTest(TestCase):
    """toto.metering must not have views, urls, templates, or billing imports."""

    def _check_no_forbidden(self, path, forbidden):
        import ast
        with open(path) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in getattr(node, "names", []):
                    self.assertFalse(
                        any(f in alias.name for f in forbidden),
                        f"{path}: forbidden import '{alias.name}'",
                    )
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(
                        any(f in node.module for f in forbidden),
                        f"{path}: forbidden import from '{node.module}'",
                    )

    def test_no_billing_imports_in_services(self):
        import os
        self._check_no_forbidden(
            os.path.join(os.path.dirname(__file__), "services.py"),
            {"tariffs", "assets", "invoice", "budget", "vault", "vod", "ravioli", "steven"},
        )

    def test_no_billing_imports_in_quotas(self):
        import os
        self._check_no_forbidden(
            os.path.join(os.path.dirname(__file__), "quotas.py"),
            {"tariffs", "assets", "invoice", "budget", "vault", "vod", "ravioli", "steven"},
        )

    def test_no_billing_imports_in_primitives(self):
        import os
        self._check_no_forbidden(
            os.path.join(os.path.dirname(__file__), "primitives.py"),
            {"tariffs", "assets", "invoice", "budget", "vault", "vod", "ravioli", "steven"},
        )

    def test_no_views_module(self):
        import os
        self.assertFalse(
            os.path.exists(os.path.join(os.path.dirname(__file__), "views.py")),
            "toto.metering must not have views.py",
        )

    def test_no_urls_module(self):
        import os
        self.assertFalse(
            os.path.exists(os.path.join(os.path.dirname(__file__), "urls.py")),
            "toto.metering must not have urls.py",
        )

    def test_no_templates_directory(self):
        import os
        self.assertFalse(
            os.path.isdir(os.path.join(os.path.dirname(__file__), "templates")),
            "toto.metering must not have a templates/ directory",
        )


# ===========================================================================
# AbstractUsageEvent validation
# ===========================================================================

class AbstractUsageEventCleanTest(TestCase):
    def test_positive_quantity_is_valid(self):
        e = _event(quantity=Decimal("0.001"))
        e.full_clean()  # should not raise

    def test_zero_quantity_rejected(self):
        from django.core.exceptions import ValidationError
        e = _event(quantity=Decimal("0"))
        with self.assertRaises(ValidationError) as cm:
            e.full_clean()
        self.assertIn("quantity", cm.exception.message_dict)

    def test_negative_quantity_rejected(self):
        from django.core.exceptions import ValidationError
        e = _event(quantity=Decimal("-5"))
        with self.assertRaises(ValidationError):
            e.full_clean()

    def test_is_recorded_property(self):
        from toto.metering.models import EventStatus
        e = _event()
        e.status = EventStatus.RECORDED
        self.assertTrue(e.is_recorded)
        self.assertFalse(e.is_voided)

    def test_is_voided_property(self):
        from toto.metering.models import EventStatus
        e = _event()
        e.status = EventStatus.VOIDED
        self.assertTrue(e.is_voided)
        self.assertFalse(e.is_recorded)


# ===========================================================================
# services.record_usage
# ===========================================================================

class RecordUsageTest(TestCase):
    def test_creates_event(self):
        event = SampleUsageEvent.objects.using("default")
        from toto.metering.services import record_usage
        e = record_usage(
            event_model=SampleUsageEvent,
            metric_code="test.metric",
            quantity=Decimal("5"),
            unit="req",
            enforce_quota=False,
        )
        self.assertIsNotNone(e.pk)
        self.assertEqual(e.quantity, Decimal("5"))
        self.assertEqual(e.metric_code, "test.metric")

    def test_idempotency_returns_existing(self):
        from toto.metering.services import record_usage
        e1 = record_usage(
            event_model=SampleUsageEvent,
            metric_code="test.metric",
            quantity=Decimal("3"),
            idempotency_key="idem-abc",
            enforce_quota=False,
        )
        e2 = record_usage(
            event_model=SampleUsageEvent,
            metric_code="test.metric",
            quantity=Decimal("99"),
            idempotency_key="idem-abc",
            enforce_quota=False,
        )
        self.assertEqual(e1.pk, e2.pk)
        self.assertEqual(SampleUsageEvent.objects.filter(idempotency_key="idem-abc").count(), 1)

    def test_rejects_non_positive_quantity(self):
        from django.core.exceptions import ValidationError
        from toto.metering.services import record_usage
        with self.assertRaises(ValidationError):
            record_usage(
                event_model=SampleUsageEvent,
                metric_code="test.metric",
                quantity=Decimal("0"),
                enforce_quota=False,
            )


# ===========================================================================
# services.void_usage_event
# ===========================================================================

class VoidUsageEventTest(TestCase):
    def setUp(self):
        from toto.metering.services import record_usage
        self.event = record_usage(
            event_model=SampleUsageEvent,
            metric_code="test.metric",
            quantity=Decimal("1"),
            enforce_quota=False,
        )

    def test_void_sets_status(self):
        from toto.metering.models import EventStatus
        from toto.metering.services import void_usage_event
        void_usage_event(self.event, reason="test reason")
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, EventStatus.VOIDED)
        self.assertEqual(self.event.metadata["void_reason"], "test reason")

    def test_void_does_not_delete(self):
        pk = self.event.pk
        from toto.metering.services import void_usage_event
        void_usage_event(self.event)
        self.assertTrue(SampleUsageEvent.objects.filter(pk=pk).exists())


# ===========================================================================
# services.supersede_usage_event
# ===========================================================================

class SupersedeUsageEventTest(TestCase):
    def test_supersede_sets_status_and_metadata(self):
        from toto.metering.models import EventStatus
        from toto.metering.services import record_usage, supersede_usage_event
        old = record_usage(
            event_model=SampleUsageEvent, metric_code="test.metric",
            quantity=Decimal("1"), enforce_quota=False,
        )
        new = record_usage(
            event_model=SampleUsageEvent, metric_code="test.metric",
            quantity=Decimal("2"), enforce_quota=False,
        )
        supersede_usage_event(old, replacement=new, reason="correction")
        old.refresh_from_db()
        self.assertEqual(old.status, EventStatus.SUPERSEDED)
        self.assertEqual(old.metadata["superseded_by"], str(new.pk))
        self.assertEqual(old.metadata["supersede_reason"], "correction")


# ===========================================================================
# quotas.quota_window
# ===========================================================================

class QuotaWindowTest(TestCase):
    def _quota(self, period, rolling=None):
        return SampleUsageQuota(
            code="qw", name="qw",
            metric_code="test.metric",
            limit_quantity=Decimal("100"),
            period=period,
            rolling_seconds=rolling,
        )

    def test_lifetime_returns_none_none_when_no_bounds(self):
        q = self._quota("lifetime")
        from toto.metering.quotas import quota_window
        start, end = quota_window(q)
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_daily_covers_24h(self):
        from datetime import timedelta
        from toto.metering.quotas import quota_window
        q = self._quota("daily")
        at = timezone.now().replace(hour=15)
        start, end = quota_window(q, at)
        self.assertEqual(start.hour, 0)
        self.assertEqual((end - start), timedelta(days=1))

    def test_monthly_starts_day_1(self):
        from toto.metering.quotas import quota_window
        q = self._quota("monthly")
        at = timezone.now().replace(day=15)
        start, end = quota_window(q, at)
        self.assertEqual(start.day, 1)
        self.assertGreater(end, start)

    def test_rolling_window(self):
        from datetime import timedelta
        from toto.metering.quotas import quota_window
        q = self._quota("rolling", rolling=3600)
        at = timezone.now()
        start, end = quota_window(q, at)
        self.assertAlmostEqual((at - start).total_seconds(), 3600, delta=1)
        self.assertEqual(end, at)

    def test_yearly_starts_jan_1(self):
        from toto.metering.quotas import quota_window
        q = self._quota("yearly")
        at = timezone.now()
        start, end = quota_window(q, at)
        self.assertEqual(start.month, 1)
        self.assertEqual(start.day, 1)
        self.assertEqual(end.year, start.year + 1)


# ===========================================================================
# quotas.evaluate_quota — subject / block / warn / track
# ===========================================================================

class EvaluateQuotaTest(TestCase):
    def setUp(self):
        # Create 5 events to consume quota
        from toto.metering.services import record_usage
        for i in range(5):
            record_usage(
                event_model=SampleUsageEvent,
                metric_code="test.metric",
                quantity=Decimal("1"),
                enforce_quota=False,
            )

    def test_global_quota_applies_to_all_subjects(self):
        from toto.metering.quotas import evaluate_quota
        _make_quota(code="global-q", limit=6, period="lifetime", mode="block")
        # 5 already used; requesting 2 more exceeds 6
        decision = evaluate_quota(
            event_model=SampleUsageEvent,
            quota_model=SampleUsageQuota,
            metric_code="test.metric",
            quantity=Decimal("2"),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.quota_code, "global-q")

    def test_subject_quota_applies_only_to_exact_subject(self):
        from toto.metering.quotas import evaluate_quota
        _make_quota(code="subj-q", limit=1, period="lifetime", mode="block",
                    subject_type="user", subject_id="A")
        # Subject A has 0 recorded events; quota limit=1 → requesting 2 > 1
        decision_a = evaluate_quota(
            event_model=SampleUsageEvent,
            quota_model=SampleUsageQuota,
            metric_code="test.metric",
            quantity=Decimal("2"),
            subject_type="user", subject_id="A",
        )
        self.assertFalse(decision_a.allowed)
        # Subject B: no applicable quota → allowed
        decision_b = evaluate_quota(
            event_model=SampleUsageEvent,
            quota_model=SampleUsageQuota,
            metric_code="test.metric",
            quantity=Decimal("99"),
            subject_type="user", subject_id="B",
        )
        self.assertTrue(decision_b.allowed)

    def test_block_quota_raises_quota_exceeded(self):
        from toto.metering.primitives import QuotaExceeded
        from toto.metering.services import record_usage
        _make_quota(code="block-q", limit=5, period="lifetime", mode="block")
        with self.assertRaises(QuotaExceeded) as cm:
            record_usage(
                event_model=SampleUsageEvent,
                quota_model=SampleUsageQuota,
                metric_code="test.metric",
                quantity=Decimal("1"),
                enforce_quota=True,
            )
        self.assertFalse(cm.exception.decision.allowed)
        # No new event created
        self.assertEqual(SampleUsageEvent.objects.count(), 5)

    def test_warn_quota_allows_event(self):
        from toto.metering.services import record_usage
        _make_quota(code="warn-q", limit=5, period="lifetime", mode="warn")
        event = record_usage(
            event_model=SampleUsageEvent,
            quota_model=SampleUsageQuota,
            metric_code="test.metric",
            quantity=Decimal("1"),
            enforce_quota=True,
        )
        self.assertIsNotNone(event.pk)
        from toto.metering.models import EventStatus
        self.assertEqual(event.status, EventStatus.RECORDED)

    def test_track_quota_never_blocks(self):
        from toto.metering.services import record_usage
        _make_quota(code="track-q", limit=1, period="lifetime", mode="track")
        event = record_usage(
            event_model=SampleUsageEvent,
            quota_model=SampleUsageQuota,
            metric_code="test.metric",
            quantity=Decimal("9999"),
            enforce_quota=True,
        )
        self.assertIsNotNone(event.pk)

    def test_no_quotas_returns_allowed(self):
        from toto.metering.quotas import evaluate_quota
        decision = evaluate_quota(
            event_model=SampleUsageEvent,
            quota_model=SampleUsageQuota,
            metric_code="test.metric",
            quantity=Decimal("1"),
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.quota_code, "")

    def test_monthly_quota_isolates_periods(self):
        from datetime import timedelta
        from toto.metering.services import record_usage
        from toto.metering.quotas import evaluate_quota
        _make_quota(code="month-q", limit=5, period="monthly", mode="block")
        last_month = timezone.now() - timedelta(days=35)
        # Record 5 events last month (outside window)
        for _ in range(5):
            record_usage(
                event_model=SampleUsageEvent,
                quota_model=SampleUsageQuota,
                metric_code="test.metric",
                quantity=Decimal("1"),
                occurred_at=last_month,
                enforce_quota=False,
            )
        # This month: 0 used → 5 within limit → should be allowed
        decision = evaluate_quota(
            event_model=SampleUsageEvent,
            quota_model=SampleUsageQuota,
            metric_code="test.metric",
            quantity=Decimal("5"),
        )
        self.assertTrue(decision.allowed)


# ===========================================================================
# registry
# ===========================================================================

class MetricRegistryTest(TestCase):
    def test_register_and_get(self):
        from toto.metering.registry import MetricRegistry
        from toto.metering.primitives import MetricSpec
        reg = MetricRegistry()
        spec = MetricSpec(code="foo.bar", name="Foo Bar", namespace="foo")
        reg.register(spec)
        self.assertEqual(reg.get("foo.bar"), spec)

    def test_by_namespace(self):
        from toto.metering.registry import MetricRegistry
        from toto.metering.primitives import MetricSpec
        reg = MetricRegistry()
        reg.register(MetricSpec(code="a.b", name="AB", namespace="ns1"))
        reg.register(MetricSpec(code="c.d", name="CD", namespace="ns2"))
        self.assertEqual(len(reg.by_namespace("ns1")), 1)
        self.assertEqual(reg.by_namespace("ns1")[0].code, "a.b")

    def test_get_unknown_returns_none(self):
        from toto.metering.registry import MetricRegistry
        reg = MetricRegistry()
        self.assertIsNone(reg.get("no.exist"))


# ===========================================================================
# utils.safe_record_usage
# ===========================================================================

class SafeRecordUsageTest(TestCase):
    def test_returns_none_without_event_model(self):
        from toto.metering.utils import safe_record_usage
        result = safe_record_usage(metric_code="test.metric", quantity=1)
        self.assertIsNone(result)

    def test_returns_none_when_metering_disabled(self):
        from unittest.mock import patch
        from toto.metering.utils import safe_record_usage
        with patch("toto.metering.utils.metering_enabled", return_value=False):
            result = safe_record_usage(
                event_model=SampleUsageEvent,
                metric_code="test.metric",
                quantity=1,
            )
        self.assertIsNone(result)

    def test_swallows_exception_and_returns_none(self):
        from toto.metering.utils import safe_record_usage
        # Passing quantity=0 will raise ValidationError inside record_usage
        result = safe_record_usage(
            event_model=SampleUsageEvent,
            metric_code="test.metric",
            quantity=0,
        )
        self.assertIsNone(result)

    def test_records_successfully_with_event_model(self):
        from toto.metering.utils import safe_record_usage
        event = safe_record_usage(
            event_model=SampleUsageEvent,
            metric_code="test.metric",
            quantity=Decimal("7"),
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.quantity, Decimal("7"))
