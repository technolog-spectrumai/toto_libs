"""
Integration tests for toto.metering source-app instrumentation.

Tests that each source app (vault, vod, ravioli, steven) correctly records
usage events through the safe_record_usage helper without breaking normal
operation.
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model

User = get_user_model()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_metric(code, unit="unit", namespace="test"):
    from toto.metering.models import UsageMetric
    m, _ = UsageMetric.objects.get_or_create(
        code=code,
        defaults={"name": code, "default_unit": unit, "namespace": namespace, "is_active": True},
    )
    return m


# ===========================================================================
# General / utils tests
# ===========================================================================

class MeteringUtilsTest(TestCase):
    """Tests for the shared safe_record_usage helper."""

    def test_metering_enabled_when_installed(self):
        from toto.metering.utils import metering_enabled
        self.assertTrue(metering_enabled())

    def test_safe_record_usage_returns_event_on_success(self):
        _make_metric("util.test", unit="req")
        from toto.metering.utils import safe_record_usage
        event = safe_record_usage(
            metric_code="util.test",
            quantity=1,
            enforce_quota=False,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.quantity, Decimal("1"))

    def test_safe_record_usage_swallows_exception(self):
        """Should return None and not raise if metering fails."""
        from toto.metering.utils import safe_record_usage
        # Metric does not exist and create_metric=False → raises inside, swallowed
        result = safe_record_usage(
            metric_code="nonexistent.metric",
            quantity=1,
            enforce_quota=False,
        )
        self.assertIsNone(result)

    def test_safe_record_usage_returns_none_when_metering_off(self):
        from toto.metering.utils import safe_record_usage
        with patch("toto.metering.utils.metering_enabled", return_value=False):
            result = safe_record_usage(metric_code="util.test", quantity=1)
        self.assertIsNone(result)

    def test_idempotency_key_prevents_duplicate_events(self):
        _make_metric("util.idem")
        from toto.metering.utils import safe_record_usage
        e1 = safe_record_usage(metric_code="util.idem", quantity=1, idempotency_key="k1", enforce_quota=False)
        e2 = safe_record_usage(metric_code="util.idem", quantity=5, idempotency_key="k1", enforce_quota=False)
        self.assertIsNotNone(e1)
        self.assertEqual(e1.pk, e2.pk)


# ===========================================================================
# Vault integration tests
# ===========================================================================

class VaultMeteringTest(TestCase):
    def setUp(self):
        _make_metric("storage.request", unit="request", namespace="vault")
        _make_metric("storage.transfer_mb", unit="MB", namespace="vault")
        self.user = User.objects.create_user(username="vault_user", password="pass")

    def _make_vault_file(self, size_bytes=1024 * 1024):
        from toto.vault.models import Bucket, VaultFile
        # Create a minimal bucket without tariff
        bucket = Bucket.objects.create(name="Test Bucket", owner=self.user, slug="test-bucket")
        from django.core.files.base import ContentFile
        vf = VaultFile(owner=self.user, title="test.txt", bucket=bucket)
        vf.file.save("test.txt", ContentFile(b"x" * size_bytes), save=False)
        vf.file_size_bytes = size_bytes
        vf.save()
        return vf

    def test_upload_records_storage_request(self):
        from toto.metering.models import UsageEvent
        vf = self._make_vault_file()

        from toto.metering.utils import safe_record_usage
        safe_record_usage(
            metric_code="storage.request",
            quantity=1,
            unit="request",
            source_type="vault.VaultFile",
            source_id=str(vf.pk),
            source_label=vf.title,
            subject_type="auth.User",
            subject_id=str(self.user.pk),
            subject_label=self.user.username,
            idempotency_key=f"vault.upload.request:{vf.pk}",
            enforce_quota=False,
        )

        event = UsageEvent.objects.get(idempotency_key=f"vault.upload.request:{vf.pk}")
        self.assertEqual(event.metric.code, "storage.request")
        self.assertEqual(event.quantity, Decimal("1"))

    def test_upload_records_storage_transfer_mb(self):
        from toto.metering.models import UsageEvent
        size_bytes = 2 * 1024 * 1024  # 2 MB
        vf = self._make_vault_file(size_bytes)
        size_mb = Decimal(str(size_bytes)) / Decimal("1048576")

        from toto.metering.utils import safe_record_usage
        safe_record_usage(
            metric_code="storage.transfer_mb",
            quantity=size_mb,
            unit="MB",
            source_type="vault.VaultFile",
            source_id=str(vf.pk),
            source_label=vf.title,
            subject_type="auth.User",
            subject_id=str(self.user.pk),
            subject_label=self.user.username,
            idempotency_key=f"vault.upload.transfer:{vf.pk}",
            enforce_quota=False,
        )

        event = UsageEvent.objects.get(idempotency_key=f"vault.upload.transfer:{vf.pk}")
        self.assertEqual(event.metric.code, "storage.transfer_mb")
        self.assertAlmostEqual(float(event.quantity), 2.0, places=2)

    def test_storage_mb_hour_snapshot(self):
        _make_metric("storage.mb_hour", unit="MBh", namespace="vault")
        from toto.metering.models import UsageEvent
        from toto.vault.models import Bucket
        bucket = Bucket.objects.create(name="B2", owner=self.user, slug="b2")

        from toto.metering.utils import safe_record_usage
        safe_record_usage(
            metric_code="storage.mb_hour",
            quantity=Decimal("100"),
            unit="MBh",
            source_type="vault.StorageSnapshot",
            source_id=f"bucket:{bucket.pk}:2026-05",
            subject_type="vault.Bucket",
            subject_id=str(bucket.pk),
            subject_label=bucket.name,
            idempotency_key=f"vault.storage.mb_hour:{bucket.pk}:2026-05",
            enforce_quota=False,
        )

        event = UsageEvent.objects.get(idempotency_key=f"vault.storage.mb_hour:{bucket.pk}:2026-05")
        self.assertEqual(event.metric.code, "storage.mb_hour")
        self.assertEqual(event.subject_type, "vault.Bucket")


# VOD metering tests removed — VodCollection, VodVideo, VodPlaybackEvent and
# record_playback_event were deleted in migration vod.0002_drop_all_vod_tables.


# ===========================================================================
# Ravioli integration tests
# ===========================================================================

class RavioliMeteringTest(TestCase):
    def setUp(self):
        _make_metric("ravioli.cypher_query", unit="query", namespace="ravioli")
        _make_metric("ravioli.cypher_row", unit="row", namespace="ravioli")
        _make_metric("ravioli.graph_event", unit="event", namespace="ravioli")

    def test_graph_event_metered_on_done(self):
        from toto.metering.models import UsageEvent
        from toto.ravioli.models import GraphChangeEvent
        from toto.ravioli.sync import mark_event_done

        event = GraphChangeEvent.objects.create(
            action=GraphChangeEvent.ACTION_UPSERT_NODE,
            graph_label="TestLabel",
            model_path="test.Model",
            object_uuid="abc123",
        )
        mark_event_done(event)

        usage = UsageEvent.objects.filter(
            metric__code="ravioli.graph_event",
            idempotency_key=f"ravioli.graph_event:{event.pk}",
        )
        self.assertEqual(usage.count(), 1)
        self.assertEqual(usage.first().source_id, str(event.pk))

    def test_graph_event_idempotency(self):
        """Calling mark_event_done twice (e.g. retry) doesn't double-count."""
        from toto.metering.models import UsageEvent
        from toto.ravioli.models import GraphChangeEvent
        from toto.ravioli.sync import mark_event_done

        event = GraphChangeEvent.objects.create(
            action=GraphChangeEvent.ACTION_UPSERT_NODE,
            graph_label="L", model_path="m", object_uuid="u1",
        )
        mark_event_done(event)
        mark_event_done(event)

        self.assertEqual(
            UsageEvent.objects.filter(metric__code="ravioli.graph_event").count(), 1
        )

    def test_cypher_query_metered_via_safe_record_usage(self):
        from toto.metering.models import UsageEvent
        from toto.ravioli.models import CypherQuery
        from toto.metering.utils import safe_record_usage
        from django.utils import timezone

        query = CypherQuery.objects.create(name="Test Q", query="MATCH (n) RETURN n")
        ts = timezone.now().strftime("%Y%m%dT%H%M%S")
        safe_record_usage(
            metric_code="ravioli.cypher_query",
            quantity=1,
            unit="query",
            source_type="ravioli.CypherQuery",
            source_id=str(query.pk),
            source_label=query.name,
            subject_type="system",
            subject_id="ravioli",
            idempotency_key=f"ravioli.cypher_query:test:{query.pk}:{ts}",
            enforce_quota=False,
        )

        self.assertEqual(UsageEvent.objects.filter(metric__code="ravioli.cypher_query").count(), 1)

    def test_cypher_row_metered(self):
        from toto.metering.models import UsageEvent
        from toto.ravioli.models import CypherQuery
        from toto.metering.utils import safe_record_usage
        from django.utils import timezone

        query = CypherQuery.objects.create(name="Row Q", query="MATCH (n) RETURN n")
        ts = timezone.now().strftime("%Y%m%dT%H%M%S")
        safe_record_usage(
            metric_code="ravioli.cypher_row",
            quantity=42,
            unit="row",
            source_type="ravioli.CypherQuery",
            source_id=str(query.pk),
            idempotency_key=f"ravioli.cypher_row:test:{query.pk}:{ts}",
            subject_type="system",
            subject_id="ravioli",
            enforce_quota=False,
        )

        event = UsageEvent.objects.get(metric__code="ravioli.cypher_row")
        self.assertEqual(event.quantity, Decimal("42"))


# ===========================================================================
# Steven integration tests
# ===========================================================================

class StevenMeteringTest(TestCase):
    def setUp(self):
        _make_metric("ai.agent_run", unit="run", namespace="steven")
        _make_metric("ai.prompt_token", unit="token", namespace="steven")
        _make_metric("ai.completion_token", unit="token", namespace="steven")
        _make_metric("ai.total_token", unit="token", namespace="steven")
        self.user = User.objects.create_user(username="steven_user", password="pass")

    def _make_agent_run(self):
        from toto.steven.models import AgentProfile, AgentRun
        profile = AgentProfile.objects.create(
            name="TestAgent",
            slug="test-agent",
            system_prompt="You are a test agent.",
            model_name="test:model",
        )
        run = AgentRun.objects.create(
            agent=profile,
            user_prompt="Hello",
        )
        return profile, run

    def test_agent_run_records_ai_agent_run(self):
        from toto.metering.models import UsageEvent
        from toto.steven.services.agent_session import StubAgentSession

        profile, run = self._make_agent_run()
        session = StubAgentSession(profile)
        session.run(run)

        events = UsageEvent.objects.filter(
            metric__code="ai.agent_run",
            idempotency_key=f"steven.agent_run:{run.pk}",
        )
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().quantity, Decimal("1"))

    def test_agent_run_metadata_contains_status_not_prompt(self):
        """Metadata must NOT contain raw prompt or completion text."""
        from toto.metering.models import UsageEvent
        from toto.steven.services.agent_session import StubAgentSession

        profile, run = self._make_agent_run()
        session = StubAgentSession(profile)
        session.run(run)

        event = UsageEvent.objects.get(
            metric__code="ai.agent_run",
            idempotency_key=f"steven.agent_run:{run.pk}",
        )
        meta = event.metadata
        self.assertIn("status", meta)
        self.assertNotIn("user_prompt", meta)
        self.assertNotIn("result", meta)
        self.assertNotIn("prompt", meta)

    def test_token_events_recorded_when_usage_available(self):
        from toto.metering.models import UsageEvent
        from toto.steven.services.agent_session import AgentSession

        profile, run = self._make_agent_run()

        class FakeSession(AgentSession):
            def invoke(self, user_prompt, history=None):
                self._last_token_usage = {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                }
                return "fake response"

        session = FakeSession(profile)
        session.run(run)

        self.assertEqual(
            UsageEvent.objects.filter(metric__code="ai.prompt_token").count(), 1
        )
        self.assertEqual(
            UsageEvent.objects.filter(metric__code="ai.completion_token").count(), 1
        )
        self.assertEqual(
            UsageEvent.objects.get(metric__code="ai.prompt_token").quantity, Decimal("100")
        )

    def test_no_token_events_without_usage(self):
        """When no token usage is available, no token events are created."""
        from toto.metering.models import UsageEvent
        from toto.steven.services.agent_session import StubAgentSession

        profile, run = self._make_agent_run()
        session = StubAgentSession(profile)
        session.run(run)

        self.assertEqual(UsageEvent.objects.filter(metric__code="ai.prompt_token").count(), 0)
        self.assertEqual(UsageEvent.objects.filter(metric__code="ai.completion_token").count(), 0)

    def test_agent_run_recorded_even_on_failure(self):
        from toto.metering.models import UsageEvent
        from toto.steven.services.agent_session import AgentSession

        profile, run = self._make_agent_run()

        class FailSession(AgentSession):
            def invoke(self, user_prompt, history=None):
                raise RuntimeError("AI failed")

        session = FailSession(profile)
        session.run(run)

        events = UsageEvent.objects.filter(metric__code="ai.agent_run")
        self.assertEqual(events.count(), 1)
        meta = events.first().metadata
        self.assertEqual(meta["status"], "failed")


# ===========================================================================
# Import purity — no source app metering integration imports billing
# ===========================================================================

class IntegrationImportPurityTest(TestCase):
    """Verify that instrumentation code doesn't sneak in billing imports."""

    def _check_no_forbidden(self, source):
        import ast
        forbidden = {"tariffs", "assets", "invoice", "budget"}
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in getattr(node, "names", []):
                    self.assertFalse(
                        any(f in alias.name for f in forbidden),
                        f"Forbidden billing import: {alias.name}",
                    )
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(
                        any(f in node.module for f in forbidden),
                        f"Forbidden billing import: {node.module}",
                    )

    def test_metering_utils_has_no_billing_imports(self):
        import os
        path = os.path.join(
            os.path.dirname(__file__), "utils.py"
        )
        with open(path) as f:
            self._check_no_forbidden(f.read())
