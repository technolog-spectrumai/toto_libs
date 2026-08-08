"""The time-limit registry and its economy-degrading façade."""

from django.test import TestCase

from toto.quota import times
from toto.quota.times import DuplicateTimeLimit, TimeLimit, TimeLimitRegistry

from ..models import TimeGrant
from .factories import make_user


def declare(key="test.dial", free=100, ceiling=1000, scope="user", scope_model=""):
    return times.registry.register(TimeLimit(
        key=key, label="Test dial", app_label="tax", scope=scope,
        free_seconds=free, ceiling_seconds=ceiling, scope_model=scope_model,
        scope_owner_attr="owner_id",
    ))


class RegistrySnapshotMixin:
    """Tests mutate the global registry; put it back afterwards."""

    def setUp(self):
        super().setUp()
        self._snapshot = dict(times.registry._limits)

    def tearDown(self):
        times.registry._limits.clear()
        times.registry._limits.update(self._snapshot)
        super().tearDown()


class RegistryTests(RegistrySnapshotMixin, TestCase):
    def test_register_and_duplicate(self):
        reg = TimeLimitRegistry()
        limit = TimeLimit(key="a.b", label="A", app_label="a", free_seconds=1,
                          ceiling_seconds=2)
        reg.register(limit)
        reg.register(limit)  # identical re-registration is fine
        with self.assertRaises(DuplicateTimeLimit):
            reg.register(TimeLimit(key="a.b", label="B", app_label="b",
                                   free_seconds=9, ceiling_seconds=99))

    def test_v1_declaration_lives_in_tax(self):
        # The demurrage metric itself is declared; the four consumer dials
        # live in apps this suite does not install.
        from toto.quota.metrics import registry as metric_registry

        self.assertIsNotNone(metric_registry.get("time.hold"))


class FacadeTests(RegistrySnapshotMixin, TestCase):
    def setUp(self):
        super().setUp()
        declare()
        self.user = make_user("alice")

    def test_default_without_grant(self):
        self.assertEqual(times.effective_seconds("test.dial", user=self.user), 100)

    def test_grant_is_honored_and_clamped(self):
        TimeGrant.objects.create(user=self.user, key="test.dial", seconds=500)
        self.assertEqual(times.effective_seconds("test.dial", user=self.user), 500)

        TimeGrant.objects.filter(user=self.user).update(seconds=99999)
        self.assertEqual(times.effective_seconds("test.dial", user=self.user), 1000)

        TimeGrant.objects.filter(user=self.user).update(seconds=1)
        self.assertEqual(times.effective_seconds("test.dial", user=self.user), 100)

    def test_unknown_key_is_zero(self):
        self.assertEqual(times.effective_seconds("no.such.dial"), 0)
        self.assertEqual(times.free_seconds("no.such.dial"), 0)

    def test_scoped_grant_ignores_user(self):
        declare(key="test.scoped", scope="workspace", scope_model="vault.Bucket")
        TimeGrant.objects.create(user=self.user, key="test.scoped",
                                 scope_id=42, seconds=700)
        self.assertEqual(
            times.effective_seconds("test.scoped", scope_id=42), 700)
        self.assertEqual(
            times.effective_seconds("test.scoped", scope_id=43), 100)

    def test_bulk_effective_seconds(self):
        declare(key="test.scoped", scope="workspace", scope_model="vault.Bucket")
        TimeGrant.objects.create(user=self.user, key="test.scoped",
                                 scope_id=1, seconds=500)
        TimeGrant.objects.create(user=self.user, key="test.scoped",
                                 scope_id=2, seconds=99999)

        limits = times.bulk_effective_seconds("test.scoped", scope_ids=[1, 2, 3])

        self.assertEqual(limits, {1: 500, 2: 1000})  # 3 absent → caller default

    def test_dial_shape(self):
        TimeGrant.objects.create(user=self.user, key="test.dial", seconds=460)

        dial = times.dial("test.dial", user=self.user)

        self.assertEqual(dial["effective_seconds"], 460)
        self.assertEqual(dial["free_seconds"], 100)
        self.assertEqual(dial["ceiling_seconds"], 1000)
        self.assertEqual(dial["extension_hours"], 0.1)
        self.assertFalse(dial["priced"])
        self.assertIsNone(dial["daily_estimate"])
        # tax is installed in this suite, so the URLs resolve.
        self.assertTrue(dial["set_url"])
        self.assertTrue(dial["manage_url"])

    def test_clear_scope(self):
        declare(key="test.scoped", scope="workspace", scope_model="vault.Bucket")
        TimeGrant.objects.create(user=self.user, key="test.scoped",
                                 scope_id=7, seconds=900)
        TimeGrant.objects.create(user=self.user, key="test.dial", seconds=900)

        removed = times.clear_scope("vault.Bucket", 7)

        self.assertEqual(removed, 1)
        self.assertEqual(TimeGrant.objects.count(), 1)  # the user-scoped one stays
