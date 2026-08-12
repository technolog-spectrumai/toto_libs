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

    def test_a_levy_reads_on_the_thing_it_bills(self):
        """What you hold, what is free, and what tonight costs — all on one page.

        This used to need three: "My levies" for the measurement, the
        allowances desk for the free part, and the rate desk for the price.
        """
        make_vault_file(self.alice, 2 * GB)
        self.client.force_login(self.alice)

        response = self.client.get(
            reverse("quota:metric_detail", args=["storage.gb_day"]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("storage.gb_day", content)
        # The storage provider's format_raw returns None (it is byte-shaped),
        # so the page states the measurement in BILLING units beside its label
        # rather than filesizeformat-ing the raw bytes: the number you are
        # charged on, in the unit the allowance is written in.
        self.assertIn("You are holding", content)
        self.assertIn("GB", content)
        self.assertIn("are free", content)
        self.assertIn("Free allowance", content)

    def test_the_retired_levy_pages_still_resolve(self):
        """Bookmarked, and named in the manual — so they redirect, not 404."""
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse("tax:my_levies")).status_code, 302)

    def test_staff_updates_allowance_and_active(self):
        """The allowance is a field on the levied thing now."""
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("quota:metric_detail", args=["storage.gb_day"]),
            {"action": "allowance", "allowance": "5"},
            # levy_active omitted → switched off
        )

        self.assertEqual(response.status_code, 302)
        self.rule.refresh_from_db()
        self.assertEqual(self.rule.allowance, Decimal("5"))
        self.assertFalse(self.rule.active)

    def test_negative_allowance_is_rejected_without_a_write(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("quota:metric_detail", args=["storage.gb_day"]),
            {"action": "allowance", "allowance": "-3", "levy_active": "on"})

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

        self.client.post(reverse("quota:metric_detail", args=["storage.gb_day"]),
                         {"action": "allowance", "allowance": "2.5",
                          "levy_active": "on"})

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

    def test_the_dial_roll_up_lives_on_the_thing_that_bills_held_time(self):
        """The dial table moved onto ``time.hold``, which is what it bills.

        It was a page of its own called Demurrage — with a nav chip that said
        "Time dials" and a url called demurrage, three names for one screen.
        Every dial is still raised where it is used; this is the roll-up of all
        of them, and it now sits on the metered thing whose price it pays.
        """
        from ..models import TimeGrant

        self.assertEqual(self.client.get(reverse("tax:demurrage")).status_code, 302)

        TimeGrant.objects.create(user=self.alice, key="test.dial", seconds=460)
        self.client.force_login(self.alice)

        # The old address still works — a live host has it bookmarked.
        moved = self.client.get(reverse("tax:demurrage"))
        self.assertEqual(moved.status_code, 302)
        self.assertEqual(moved["Location"], reverse("quota:metric_detail",
                                                    args=["time.hold"]))

        response = self.client.get(moved["Location"])
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("test.dial", content)
        self.assertIn("0.1 h", content)

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
        # An off-site `next` is refused and the fallback is the roll-up, which
        # is now a section of the thing that bills held time.
        self.assertEqual(response["Location"],
                         reverse("quota:metric_detail", args=["time.hold"]))

    def test_the_roll_up_is_reachable_without_being_staff(self):
        """A member has every reason to be there — it is their bill."""
        self.client.force_login(self.alice)
        response = self.client.get(reverse("quota:metric_detail", args=["time.hold"]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("time dial", response.content.decode().lower())
