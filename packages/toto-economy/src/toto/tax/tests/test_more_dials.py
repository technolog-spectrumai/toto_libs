"""Time dials at the edges: the one POST door's refusals, the roll-up's labels
for scopes that moved or vanished, and the provider that bills them."""

from decimal import Decimal

from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.urls import reverse

from toto.assets.testing import LedgerTestCase as TestCase
from toto.quota.levy import registry as levy_registry
from toto.quota.times import TimeLimit, registry as time_registry

from .. import timegrants
from ..models import TimeGrant
from .factories import make_gas_asset, make_user
from .test_times import RegistrySnapshotMixin

HOUR = 3600


def _declare(key, *, free=0, ceiling=10 * HOUR, scope_model=""):
    time_registry.register(TimeLimit(
        key=key, label=f"Dial {key}", app_label="tax",
        scope="workspace" if scope_model else "user",
        free_seconds=free, ceiling_seconds=ceiling, scope_model=scope_model,
        scope_owner_attr="owner_id",
    ))


def _bucket(owner, slug):
    from toto.vault.models import Bucket

    return Bucket.objects.create(name=slug, slug=slug, owner=owner)


class SetGrantEdgeTests(RegistrySnapshotMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.alice = make_user("alice")
        _declare("more.user", free=HOUR, ceiling=5 * HOUR)
        _declare("more.ghost_ws", scope_model="nowhere.Workspace")

    def test_an_undeclared_dial_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "No time dial"):
            timegrants.set_grant(actor=self.alice, key="never.declared",
                                 seconds=10)
        self.assertFalse(TimeGrant.objects.exists())

    def test_a_workspace_dial_whose_app_is_not_installed_is_not_found(self):
        with self.assertRaises(Http404):
            timegrants.set_grant(actor=self.alice, key="more.ghost_ws",
                                 seconds=10, scope_id=1)

    def test_the_bounds_are_inclusive(self):
        at_ceiling = timegrants.set_grant(actor=self.alice, key="more.user",
                                          seconds=5 * HOUR)
        self.assertEqual(at_ceiling.seconds, 5 * HOUR)
        with self.assertRaises(ValidationError):
            timegrants.set_grant(actor=self.alice, key="more.user",
                                 seconds=5 * HOUR + 1)
        with self.assertRaises(ValidationError):
            timegrants.set_grant(actor=self.alice, key="more.user",
                                 seconds=HOUR - 1)

    def test_seconds_given_as_text_are_taken_as_a_number(self):
        grant = timegrants.set_grant(actor=self.alice, key="more.user",
                                     seconds="7200")
        self.assertEqual(grant.seconds, 7200)

    def test_setting_one_users_dial_leaves_anothers_alone(self):
        bob = make_user("bob")
        timegrants.set_grant(actor=bob, key="more.user", seconds=2 * HOUR)
        timegrants.set_grant(actor=self.alice, key="more.user", seconds=3 * HOUR)
        timegrants.clear_grant(actor=self.alice, key="more.user")
        self.assertEqual(list(TimeGrant.objects.values_list("user__username", "seconds")),
                         [("bob", 2 * HOUR)])


class RollUpTests(RegistrySnapshotMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.alice = make_user("alice")
        _declare("more.user", free=HOUR, ceiling=5 * HOUR)
        _declare("more.bucket", scope_model="vault.Bucket")
        _declare("more.ghost_ws", scope_model="nowhere.Workspace")

    def test_a_grant_whose_dial_left_the_registry_is_not_listed(self):
        TimeGrant.objects.create(user=self.alice, key="retired.dial", seconds=99)
        TimeGrant.objects.create(user=self.alice, key="more.user", seconds=2 * HOUR)
        data = timegrants.rows_for_user(self.alice)
        self.assertEqual([row["grant"].key for row in data["rows"]], ["more.user"])
        self.assertEqual(data["total_extra_hours"], 1.0)

    def test_scope_labels_name_the_workspace_or_say_what_happened_to_it(self):
        live = _bucket(self.alice, "live-bucket")
        gone = _bucket(self.alice, "gone-bucket")
        TimeGrant.objects.create(user=self.alice, key="more.bucket",
                                 scope_id=live.pk, seconds=HOUR)
        TimeGrant.objects.create(user=self.alice, key="more.bucket",
                                 scope_id=gone.pk, seconds=HOUR)
        TimeGrant.objects.create(user=self.alice, key="more.ghost_ws",
                                 scope_id=77, seconds=HOUR)
        gone_pk = gone.pk
        gone.delete()

        labels = {(row["grant"].key, row["grant"].scope_id): row["scope_label"]
                  for row in timegrants.rows_for_user(self.alice)["rows"]}
        self.assertEqual(labels[("more.bucket", live.pk)], "live-bucket")
        self.assertEqual(labels[("more.bucket", gone_pk)], "(deleted workspace)")
        self.assertEqual(labels[("more.ghost_ws", 77)], "#77")

    def test_without_a_price_nothing_is_estimated(self):
        TimeGrant.objects.create(user=self.alice, key="more.user", seconds=3 * HOUR)
        data = timegrants.rows_for_user(self.alice)
        self.assertIsNone(data["price"])
        self.assertIsNone(data["rows"][0]["estimate"])
        self.assertIsNone(data["total_estimate"])

    def test_a_grant_at_or_below_its_free_default_costs_nothing(self):
        make_gas_asset()
        from toto.quota.metrics import registry
        from toto.tariffs.rate_card import upsert_price

        upsert_price(registry.get("time.hold"), Decimal("1"))
        TimeGrant.objects.create(user=self.alice, key="more.user", seconds=HOUR)
        row = timegrants.rows_for_user(self.alice)["rows"][0]
        self.assertEqual(row["extra_seconds"], 0)
        self.assertIsNone(row["estimate"])


class TimeHoldProviderTests(RegistrySnapshotMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.provider = levy_registry.get("time.hold")
        self.alice = make_user("alice")

    def test_hours_are_written_compactly(self):
        self.assertEqual(self.provider.format_raw(5400), "1.5 h")
        self.assertEqual(self.provider.format_raw(7200), "2 h")

    def test_a_grant_it_cannot_check_is_kept_and_billed_never_deleted_blind(self):
        _declare("more.ghost_ws", scope_model="nowhere.Workspace")
        TimeGrant.objects.create(user=self.alice, key="more.ghost_ws",
                                 scope_id=5, seconds=2 * HOUR)
        self.assertEqual(self.provider.measure(self.alice), 2 * HOUR)
        self.assertTrue(TimeGrant.objects.filter(key="more.ghost_ws").exists())

    def test_a_dial_that_left_the_registry_stops_billing(self):
        TimeGrant.objects.create(user=self.alice, key="retired.dial",
                                 seconds=9 * HOUR)
        self.assertEqual(self.provider.measure(self.alice), 0)
        self.assertEqual(list(self.provider.sample()), [])


class TimeSetDoorTests(RegistrySnapshotMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        cls.alice = make_user("alice")

    def setUp(self):
        super().setUp()
        _declare("more.user", free=HOUR, ceiling=5 * HOUR)
        _declare("more.ghost_ws", scope_model="nowhere.Workspace")
        self.client.force_login(self.alice)

    def _post(self, **data):
        return self.client.post(reverse("tax:time_set"), data)

    def _said(self, response):
        return " ".join(str(m) for m in get_messages(response.wsgi_request))

    def test_seconds_that_are_not_a_number_are_a_message_and_write_nothing(self):
        response = self._post(key="more.user", seconds="lots")
        self.assertEqual(response.status_code, 302)
        self.assertIn("invalid literal", self._said(response))
        self.assertFalse(TimeGrant.objects.exists())

    def test_an_undeclared_dial_is_a_message(self):
        response = self._post(key="never.declared", seconds="10")
        self.assertIn("No time dial", self._said(response))
        self.assertFalse(TimeGrant.objects.exists())

    def test_a_workspace_that_cannot_be_found_is_a_message_not_a_404(self):
        response = self._post(key="more.ghost_ws", seconds="10", scope_id="3")
        self.assertEqual(response.status_code, 302)
        self.assertIn("That workspace no longer exists", self._said(response))

    def test_a_non_numeric_scope_is_ignored_and_the_account_dial_is_set(self):
        self._post(key="more.user", seconds=str(2 * HOUR), scope_id="abc")
        grant = TimeGrant.objects.get()
        self.assertIsNone(grant.scope_id)
        self.assertEqual(grant.user, self.alice)

    def test_setting_the_free_default_says_it_was_reset(self):
        TimeGrant.objects.create(user=self.alice, key="more.user", seconds=3 * HOUR)
        response = self._post(key="more.user", seconds=str(HOUR))
        self.assertIn("Reset to the free default", self._said(response))
        self.assertFalse(TimeGrant.objects.exists())

    def test_a_local_next_is_followed(self):
        response = self._post(key="more.user", seconds=str(2 * HOUR),
                              next="/socialhub/")
        self.assertEqual(response["Location"], "/socialhub/")

    def test_only_a_post_can_set_a_dial(self):
        self.assertEqual(self.client.get(reverse("tax:time_set")).status_code, 405)


class RetiredPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.member = make_user("member")
        cls.staff = make_user("boss", is_staff=True)

    def test_the_old_rules_desk_is_still_staff_only(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse("tax:rules")).status_code, 403)

    def test_the_old_rules_desk_sends_staff_to_the_metered_things(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("tax:rules"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("quota:index"))

    def test_my_levies_sends_a_member_to_the_metered_things(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("tax:my_levies"))
        self.assertEqual(response["Location"], reverse("quota:index"))
