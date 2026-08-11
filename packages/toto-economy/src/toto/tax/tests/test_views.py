"""Who may read and set what on the tax pages.

The user page is owner-scoped by construction (it renders only the signed-in
user's numbers); the allowance desk is staff-only with a hard 403, never a
redirect — the rate-desk convention.
"""

from decimal import Decimal

from toto.assets.testing import LedgerTestCase as TestCase
from django.urls import reverse

from ..models import TaxRule
from .factories import GB, make_rule, make_user, make_vault_file


class TaxViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # PageProcessor 404s without one, so every rendered page needs it.
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "test", "publication_year": 2026, "active": True},
        )
        cls.alice = make_user("alice")
        cls.staff = make_user("staff", is_staff=True)

    def setUp(self):
        self.rule = make_rule(allowance="1")

    def test_my_levies_requires_login(self):
        response = self.client.get(reverse("tax:my_levies"))
        self.assertEqual(response.status_code, 302)

    def test_my_levies_shows_readable_gb_and_the_estimate(self):
        make_vault_file(self.alice, 2 * GB)
        self.client.force_login(self.alice)

        response = self.client.get(reverse("tax:my_levies"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("storage.gb_day", content)
        self.assertIn("2.0", content)  # filesizeformat of 2 GiB
        self.assertIn("Free allowance", content)

    def test_rules_is_staff_only(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse("tax:rules")).status_code, 403)

    def test_staff_updates_allowance_and_active(self):
        self.client.force_login(self.staff)

        response = self.client.post(reverse("tax:rules"), {
            "allowance__storage.gb_day": "5",
            # active checkbox omitted → switched off
        })

        self.assertEqual(response.status_code, 302)
        self.rule.refresh_from_db()
        self.assertEqual(self.rule.allowance, Decimal("5"))
        self.assertFalse(self.rule.active)

    def test_negative_allowance_is_rejected_without_a_write(self):
        self.client.force_login(self.staff)

        response = self.client.post(reverse("tax:rules"), {
            "allowance__storage.gb_day": "-3",
            "active__storage.gb_day": "on",
        })

        self.assertEqual(response.status_code, 200)
        self.rule.refresh_from_db()
        self.assertEqual(self.rule.allowance, Decimal("1"))

    def test_manual_renders_the_storage_fee_section(self):
        """The platform-rule text must render — and its links must reverse —
        on any host that installs toto.tax."""
        self.client.force_login(self.alice)

        response = self.client.get("/manual/")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Storage fee", content)
        self.assertIn("automatically", content)
        self.assertIn("chosen at random", content)
        self.assertIn("one week", content)

    def test_saving_creates_a_rule_for_a_ruleless_provider(self):
        TaxRule.objects.all().delete()
        self.client.force_login(self.staff)

        self.client.post(reverse("tax:rules"), {
            "allowance__storage.gb_day": "2.5",
            "active__storage.gb_day": "on",
        })

        rule = TaxRule.objects.get(metric_code="storage.gb_day")
        self.assertEqual(rule.allowance, Decimal("2.5"))
        self.assertTrue(rule.active)


class DemurrageViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "test", "publication_year": 2026, "active": True},
        )
        cls.alice = make_user("alice")
        cls.bob = make_user("bob")

    def setUp(self):
        from toto.quota import times
        from toto.quota.times import TimeLimit

        self._snapshot = dict(times.registry._limits)
        times.registry.register(TimeLimit(
            key="test.dial", label="Test dial", app_label="tax", scope="user",
            free_seconds=100, ceiling_seconds=1000,
        ))
        times.registry.register(TimeLimit(
            key="test.bucket_dial", label="Bucket dial", app_label="tax",
            scope="workspace", scope_model="vault.Bucket",
            scope_owner_attr="owner_id", free_seconds=100, ceiling_seconds=1000,
        ))

    def tearDown(self):
        from toto.quota import times

        times.registry._limits.clear()
        times.registry._limits.update(self._snapshot)

    def test_demurrage_requires_login_and_renders(self):
        from ..models import TimeGrant

        self.assertEqual(self.client.get(reverse("tax:demurrage")).status_code, 302)

        TimeGrant.objects.create(user=self.alice, key="test.dial", seconds=460)
        self.client.force_login(self.alice)
        response = self.client.get(reverse("tax:demurrage"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("test.dial", content)
        self.assertIn("0.1 h", content)
        self.assertIn("currently free", content.lower())

    def test_time_set_happy_path_and_reset(self):
        from ..models import TimeGrant

        self.client.force_login(self.alice)
        response = self.client.post(reverse("tax:time_set"), {
            "key": "test.dial", "seconds": "500",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TimeGrant.objects.get().seconds, 500)

        self.client.post(reverse("tax:time_set"), {"key": "test.dial", "seconds": ""})
        self.assertEqual(TimeGrant.objects.count(), 0)

    def test_time_set_rejects_out_of_bounds_without_writing(self):
        from ..models import TimeGrant

        self.client.force_login(self.alice)
        self.client.post(reverse("tax:time_set"), {"key": "test.dial", "seconds": "5000"})
        self.assertEqual(TimeGrant.objects.count(), 0)

    def test_time_set_foreign_workspace_is_403(self):
        from toto.vault.models import Bucket

        bucket = Bucket.objects.create(name="b", slug="b", owner=self.alice)
        self.client.force_login(self.bob)

        response = self.client.post(reverse("tax:time_set"), {
            "key": "test.bucket_dial", "seconds": "500",
            "scope_id": str(bucket.pk),
        })

        self.assertEqual(response.status_code, 403)

    def test_time_set_validates_next(self):
        self.client.force_login(self.alice)

        response = self.client.post(reverse("tax:time_set"), {
            "key": "test.dial", "seconds": "500",
            "next": "https://evil.example/phish",
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("tax:demurrage"))

    def test_demurrage_tab_visible_to_non_staff(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse("tax:my_levies"))
        self.assertIn(reverse("tax:demurrage"), response.content.decode())
