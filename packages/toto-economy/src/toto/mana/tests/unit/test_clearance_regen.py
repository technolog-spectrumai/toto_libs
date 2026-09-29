"""A clearance sets how fast mana refills (2026-09-28).

The fastest speed any of a member's clearances sets for a pool is theirs; no
clearance, or clearances that set none, keeps the pool's own rate; the pool's own
rate at 0 is still the operator's off switch. A community carries no speed:
clearances (``socialhub.Clearance``) are the whole of the refill axis.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from toto.mana import services
from toto.mana.tests.fixtures import economy, held, master, spend
from toto.people.models import Person
from toto.socialhub.models import Clearance

User = get_user_model()


def clearance(slug, speed=None, **speeds):
    """A clearance whose every pool refills at ``speed``, or at ``speeds``."""
    fields = {f"regen_{role}": speed for role in ("security", "compute", "storage")}
    fields.update({f"regen_{role}": value for role, value in speeds.items()})
    return Clearance.objects.create(name=slug, slug=slug, **fields)


def person(user, *clearances):
    found = Person.objects.create(user=user, display_name=user.username)
    found.clearances.add(*clearances)
    return found


@master
class ClearanceSpeedTests(TestCase):
    HOUR = timezone.now().replace(minute=30, second=0, microsecond=0)

    def setUp(self):
        economy()
        self.pools = services.pools()
        self.confidential = clearance("confidential", Decimal("12"))
        self.payroll = clearance("payroll", Decimal("8"), storage=Decimal("20"))
        self.onboarding = clearance("onboarding")                     # sets nothing
        self.ada = User.objects.create_user("ada", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")
        self.ada_person = person(self.ada, self.confidential, self.payroll)
        person(self.bob, self.onboarding)

    def rate(self, user, role):
        return services.regen_for(user, self.pools[role])

    def test_the_fastest_clearance_wins_pool_by_pool(self):
        self.assertEqual(self.rate(self.ada, "compute"), (Decimal("12"), "confidential"))
        self.assertEqual(self.rate(self.ada, "storage"), (Decimal("20"), "payroll"))

    def test_no_clearance_speed_keeps_the_pool_rate(self):
        self.assertEqual(self.rate(self.bob, "compute"), (Decimal("4"), ""))
        carol = User.objects.create_user("carol", password="pw")      # no Person at all
        self.assertEqual(self.rate(carol, "compute"), (Decimal("4"), ""))

    def test_one_run_pays_each_member_their_own_speed(self):
        for user in (self.ada, self.bob):
            spend(user, "compute", "50")
        services.regenerate_hour(at=self.HOUR)
        self.assertEqual(held(self.ada, "compute"), Decimal("62"))
        self.assertEqual(held(self.bob, "compute"), Decimal("54"))

    def test_the_same_hour_twice_pays_nobody_twice(self):
        spend(self.ada, "compute", "50")
        services.regenerate_hour(at=self.HOUR)
        services.regenerate_hour(at=self.HOUR)
        self.assertEqual(held(self.ada, "compute"), Decimal("62"))

    def test_a_fast_clearance_never_fills_past_the_maximum(self):
        spend(self.ada, "compute", "5")
        services.regenerate_hour(at=self.HOUR)
        self.assertEqual(held(self.ada, "compute"), Decimal("100"))

    def test_a_clearance_change_counts_from_the_next_hour(self):
        spend(self.bob, "compute", "50")
        services.regenerate_hour(at=self.HOUR)
        self.bob.community_profile.clearances.add(self.confidential)
        services.regenerate_hour(at=self.HOUR + timedelta(hours=1))
        self.assertEqual(held(self.bob, "compute"), Decimal("66"))       # 50 + 4 + 12

    def test_a_zero_speed_clearance_refills_nothing(self):
        stopped = clearance("stopped", Decimal("0"))
        self.bob.community_profile.clearances.add(stopped)
        spend(self.bob, "compute", "50")
        services.regenerate_hour(at=self.HOUR)
        self.assertEqual(held(self.bob, "compute"), Decimal("50"))

    def test_the_pool_rate_at_zero_switches_it_off_for_everybody(self):
        pool = self.pools["compute"]
        pool.regen_per_hour = Decimal("0")
        pool.save()
        spend(self.ada, "compute", "50")
        services.regenerate_hour(at=self.HOUR)
        self.assertEqual(held(self.ada, "compute"), Decimal("50"))

    def test_the_refusal_names_the_member_speed(self):
        from toto.mana.tests.fixtures import seed_prices
        from toto.quota.charge import InsufficientFunds, check_funds, price_for

        seed_prices()
        spend(self.ada, "compute", "99.9")
        tariff = price_for(self.ada, "workflows")
        with self.assertRaises(InsufficientFunds) as caught:
            check_funds(self.ada, tariff, "workflows.run", 1)
        self.assertIn("It refills 12 an hour", str(caught.exception))

    def test_the_member_pages_quote_the_member_speed(self):
        self.client.force_login(self.ada)
        balances = services.balances_of(self.ada)
        self.assertEqual(balances["compute"]["regen_per_hour"], Decimal("12"))
        self.assertEqual(balances["compute"]["regen_per_day"], Decimal("288"))
        page = self.client.get(reverse("mana:colour", args=["compute"])).content.decode()
        self.assertIn("Your clearance confidential sets this speed.", page)


@master
class RegenerationPageTests(TestCase):
    def setUp(self):
        economy()
        self.confidential = clearance("confidential", Decimal("12"))
        clearance("payroll", Decimal("8"))
        self.ada = User.objects.create_user("ada", password="pw")
        person(self.ada, self.confidential)
        self.bob = User.objects.create_user("bob", password="pw")
        self.root = User.objects.create_superuser("root", password="pw")

    def page(self, user):
        self.client.force_login(user)
        response = self.client.get(reverse("mana:regeneration"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_a_member_sees_their_speed_and_their_own_clearances_only(self):
        body = self.page(self.ada)
        self.assertIn("clearance confidential", body)
        self.assertIn("+12", body)
        self.assertIn('data-testid="regen-clearances"', body)
        self.assertNotIn("payroll", body)          # clearances are hidden from members
        self.assertNotIn('data-testid="regen-every"', body)

    def test_a_member_in_no_clearance_reads_the_platform_default(self):
        body = self.page(self.bob)
        self.assertIn("the platform's default (4/h)", body)
        self.assertNotIn('data-testid="regen-clearances"', body)

    def test_a_superuser_sees_every_clearance(self):
        body = self.page(self.root)
        self.assertIn('data-testid="regen-every"', body)
        self.assertIn("payroll", body)
        self.assertIn("confidential", body)

    def test_the_economy_strip_links_it(self):
        self.client.force_login(self.bob)
        body = self.client.get(reverse("mana:index")).content.decode()
        self.assertIn(reverse("mana:regeneration"), body)
