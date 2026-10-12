"""Discipline: the one mana, what spends it and what regenerates it.

The numbers are a file of the tests' own (cap 100, a first balance of 10,
an upload 0.5 and 0.1 a megabyte), whatever the host's file says.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

from django.apps import apps

if not apps.is_installed("toto.discipline"):
    raise unittest.SkipTest("toto.discipline is not installed on this host")

from django.contrib.auth import get_user_model  # noqa: E402
from django.db import IntegrityError, transaction  # noqa: E402
from django.test import TestCase, override_settings  # noqa: E402
from django.utils import timezone, translation  # noqa: E402

from toto.people.models import Person  # noqa: E402
from toto.socialhub.models import Community  # noqa: E402

from . import config, services, sigils  # noqa: E402
from .models import (Aura, CommunityAura, ManaAccount, ManaEvent, ManaKind, Sigil,  # noqa: E402
                     SigilAction, SigilEvent)

User = get_user_model()

NUMBERS = """version: 1
cap: 100
signup: 10
default_regen_per_hour: 0
prices:
  storage.request: 0.5
  storage.transfer_mb: 0.1
"""


def numbers_file(text: str = NUMBERS) -> str:
    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", prefix="discipline-",
                                         delete=False, encoding="utf-8")
    handle.write(text)
    handle.close()
    return handle.name


FILE = numbers_file()


@override_settings(DISCIPLINE_FILE=FILE, QUOTA_CHARGER="discipline")
class Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = User.objects.create_superuser("root", "r@e.com", "pw")
        cls.mia = User.objects.create_user("mia", password="pw")
        cls.noel = User.objects.create_user("noel", password="pw")
        cls.everyone = Community.objects.create(name="Everyone", slug="everyone")
        cls.guild = Community.objects.create(name="Guild", slug="guild", parent=cls.everyone)
        cls.club = Community.objects.create(name="Club", slug="club")
        cls.person = Person.objects.create(user=cls.mia, display_name="Mia")

    def events(self, user, kind=None):
        rows = ManaEvent.objects.filter(user=user)
        return rows.filter(kind=kind) if kind else rows

    def have(self, user, amount):
        services.account_for(user)
        ManaAccount.objects.filter(user=user).update(balance=D(str(amount)))


class PriceTests(Fixture):
    def test_a_listed_metric_costs_its_price_a_unit(self):
        self.assertEqual(services.price_of("storage.request"), D("0.5"))
        self.assertEqual(services.cost("storage.request", 3), D("1.5"))
        self.assertEqual(services.cost("storage.transfer_mb", D("2.5")), D("0.25"))

    def test_a_metric_that_is_not_listed_costs_nothing(self):
        self.assertIsNone(services.price_of("storage.egress_mb"))
        self.assertEqual(services.cost("storage.egress_mb", 100), 0)
        self.assertIsNone(services.spend(self.mia, "storage.egress_mb", 100))
        self.assertEqual(services.balance(self.mia), D("10"))

    def test_an_app_is_priced_when_one_of_its_metrics_is(self):
        self.assertTrue(services.priced_app("vault"))
        self.assertFalse(services.priced_app("subscriptions"))


class SpendTests(Fixture):
    def test_a_new_account_starts_with_the_first_balance_once(self):
        self.assertEqual(services.balance(self.mia), D("10"))
        services.signup(self.mia)
        self.assertEqual(services.balance(self.mia), D("10"))
        self.assertEqual(self.events(self.mia, ManaKind.SIGNUP).count(), 1)

    def test_a_spend_takes_the_cost_and_says_what_for(self):
        event = services.spend(self.mia, "storage.request", 2, source_type="vault.VaultFile",
                               source_id=7)
        self.assertEqual((event.kind, event.delta, event.balance_after),
                         (ManaKind.SPEND, D("-1"), D("9")))
        self.assertEqual((event.metric_code, event.quantity, event.source_id),
                         ("storage.request", D("2"), "7"))
        self.assertEqual(services.balance(self.mia), D("9"))

    def test_the_same_key_spends_once(self):
        first = services.spend(self.mia, "storage.request", 1, key="upload:1")
        again = services.spend(self.mia, "storage.request", 1, key="upload:1")
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(services.balance(self.mia), D("9.5"))
        # Without a key every call is a spend of its own.
        services.spend(self.mia, "storage.request", 1)
        services.spend(self.mia, "storage.request", 1)
        self.assertEqual(services.balance(self.mia), D("8.5"))

    def test_a_short_balance_is_refused_and_nothing_is_taken(self):
        self.have(self.mia, "0.3")
        with translation.override("en"), self.assertRaises(services.NotEnoughMana) as caught:
            services.spend(self.mia, "storage.request", 1)
        refusal = caught.exception
        self.assertEqual(refusal.status_code, 402)
        self.assertEqual((refusal.needed_display, refusal.have_display, refusal.shortfall_display,
                          refusal.asset_name), (D("0.5"), D("0.3"), D("0.2"), "mana"))
        text = str(refusal)
        self.assertIn("this needs 0.5 and you have 0.3", text)
        self.assertIn("Nothing refills it for you here.", text)
        self.assertIn("An administrator can grant mana.", text)
        self.assertEqual(services.balance(self.mia), D("0.3"))
        self.assertFalse(self.events(self.mia, ManaKind.SPEND).exists())

    def test_the_refusal_says_when_there_is_enough_again(self):
        CommunityAura.objects.create(community=self.club, aura=Aura.objects.create(
            name="Slow", slug="slow", regen_per_hour=D("2"), cap=D("40")))
        self.person.communities.add(self.club)
        self.have(self.mia, "1")
        with translation.override("en"), self.assertRaises(services.NotEnoughMana) as caught:
            services.check(self.mia, "storage.request", 10)       # needs 5, has 1
        self.assertIn("It refills 2 an hour: enough again in about 2 h.", str(caught.exception))
        with translation.override("en"), self.assertRaises(services.NotEnoughMana) as caught:
            services.check(self.mia, "storage.request", 100)      # needs 50, the cap is 40
        self.assertIn("more than regeneration fills to (40)", str(caught.exception))

    def test_a_refusal_is_the_charge_doors_own_kind(self):
        from toto.quota.charge import InsufficientFunds

        self.assertTrue(issubclass(services.NotEnoughMana, InsufficientFunds))

    def test_a_refusal_stands_on_the_ledgers_class_too(self):
        """Where the ledger's package is importable (not installed), the
        charge door's refusal is the ledger's class, whose amounts are
        read-only properties: Discipline's must be made on it all the same."""
        class Ledgers(Exception):
            status_code = 402
            needed_display = property(lambda self: 1 / 0)
            have_display = property(lambda self: 1 / 0)
            shortfall_display = property(lambda self: 1 / 0)

        made = type("NotEnoughMana", (Ledgers,), dict(services.NotEnoughMana.__dict__))(
            D("2"), D("0.5"), "short")
        self.assertEqual((made.needed_display, made.have_display, made.shortfall_display,
                          str(made)), (D("2"), D("0.5"), D("1.5"), "short"))

    def test_check_all_adds_the_charges_up(self):
        self.have(self.mia, "1")
        services.check(self.mia, "storage.request", 1)
        services.check(self.mia, "storage.transfer_mb", 6)
        with self.assertRaises(services.NotEnoughMana):
            services.check_all(self.mia, [("storage.request", 1, ""),
                                          ("storage.transfer_mb", 6, "MB")])

    def test_work_that_is_done_takes_what_is_there(self):
        self.have(self.mia, "0.3")
        event = services.spend(self.mia, "storage.request", 1, strict=False)
        self.assertEqual((event.delta, event.balance_after), (D("-0.3"), D("0")))
        self.assertEqual(event.metadata["short"], "0.2000")
        self.assertEqual(services.balance(self.mia), 0)

    def test_a_spend_is_given_back_once(self):
        event = services.spend(self.mia, "storage.request", 4, source_type="vault.TransferRun",
                               source_id=3)
        back = services.refund(event, reason="the copy failed")
        self.assertEqual((back.kind, back.delta), (ManaKind.REFUND, D("2")))
        self.assertEqual(services.refund(event).pk, back.pk)
        self.assertEqual(services.balance(self.mia), D("10"))
        self.assertIsNone(services.refund(back))            # a refund is not refunded

    def test_refund_for_finds_the_newest_spend_not_given_back(self):
        src = {"source_type": "vault.TransferRun", "source_id": 3}
        services.spend(self.mia, "storage.request", 1, **src)
        services.spend(self.mia, "storage.request", 2, **src)
        self.assertEqual(services.refund_for("vault.TransferRun", 3).delta, D("1"))
        self.assertEqual(services.refund_for("vault.TransferRun", 3, "storage.request").delta,
                         D("0.5"))
        self.assertIsNone(services.refund_for("vault.TransferRun", 3))
        self.assertEqual(services.balance(self.mia), D("10"))

    def test_a_grant_lands_whole_above_the_cap(self):
        event = services.grant(self.mia, 500, reason="a gift", by=self.root)
        self.assertEqual((event.kind, event.by, event.reason), (ManaKind.GRANT, self.root, "a gift"))
        self.assertEqual(services.balance(self.mia), D("510"))
        for nothing in (0, -1):
            with self.assertRaises(ValueError):
                services.grant(self.mia, nothing)

    def test_a_balance_never_goes_below_zero(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ManaAccount.objects.filter(user=self.mia).update(balance=D("-1"))


class SpeedTests(Fixture):
    def aura(self, name, rate, cap=None, *communities):
        aura = Aura.objects.create(name=name, slug=name.lower(), regen_per_hour=D(str(rate)),
                                   cap=None if cap is None else D(str(cap)))
        for community in communities:
            CommunityAura.objects.create(community=community, aura=aura)
        return aura

    def test_without_an_aura_or_a_sigil_nothing_regenerates(self):
        speed = services.speed_for(self.mia)
        self.assertEqual((speed.rate, speed.cap, speed.aura, speed.sigils), (0, D("100"), None, []))

    def test_the_aura_comes_through_the_community(self):
        slow = self.aura("Slow", 2, 40, self.club)
        self.assertEqual(services.speed_for(self.mia).rate, 0)      # not a member yet
        self.person.communities.add(self.club)
        speed = services.speed_for(self.mia)
        self.assertEqual((speed.aura, speed.community, speed.rate, speed.cap),
                         (slow, self.club, D("2"), D("40")))

    def test_an_aura_without_a_cap_takes_the_platforms(self):
        self.aura("Slow", 2, None, self.club)
        self.person.communities.add(self.club)
        self.assertEqual(services.speed_for(self.mia).cap, D("100"))

    def test_the_fastest_aura_of_several_communities_is_the_one(self):
        self.aura("Slow", 2, 40, self.club)
        fast = self.aura("Fast", 5, 60, self.guild)
        self.person.communities.add(self.club, self.guild)
        speed = services.speed_for(self.mia)
        self.assertEqual((speed.aura, speed.community, speed.rate, speed.cap),
                         (fast, self.guild, D("5"), D("60")))

    def test_the_aura_of_a_community_above_counts(self):
        above = self.aura("Above", 3, None, self.everyone)
        self.person.communities.add(self.guild)                    # guild is under everyone
        speed = services.speed_for(self.mia)
        self.assertEqual((speed.aura, speed.community, speed.rate), (above, self.guild, D("3")))

    def test_many_communities_share_one_aura_and_a_community_has_one(self):
        shared = self.aura("Shared", 4, None, self.club, self.guild)
        self.assertEqual(shared.community_links.count(), 2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CommunityAura.objects.create(community=self.club, aura=self.aura("Other", 1))

    def test_every_held_sigil_adds_to_the_aura(self):
        self.aura("Slow", 2, 40, self.club)
        self.person.communities.add(self.club)
        lantern = sigils.create("Lantern", D("1.5"), by=self.root)
        key = sigils.create("Key", D("3"), by=self.root)
        sigils.assign(lantern, self.mia, by=self.root)
        sigils.assign(key, self.mia, by=self.root)
        speed = services.speed_for(self.mia)
        self.assertEqual((speed.aura_rate, speed.rate, speed.cap), (D("2"), D("6.5"), D("40")))
        self.assertEqual(speed.parts(), {"aura": "2.0000", "sigils": {
            str(key.uuid): "3.0000", str(lantern.uuid): "1.5000"}})
        # A sigil alone regenerates too, to the platform's cap.
        sigils.assign(sigils.create("Coin", 1, by=self.root), self.noel, by=self.root)
        self.assertEqual((services.speed_for(self.noel).rate, services.speed_for(self.noel).cap),
                         (D("1"), D("100")))

    def test_set_community_aura_gives_changes_and_takes(self):
        slow, fast = self.aura("Slow", 2), self.aura("Fast", 5)
        self.assertTrue(services.set_community_aura(self.club, slow, by=self.root))
        self.assertFalse(services.set_community_aura(self.club, slow, by=self.root))
        self.assertTrue(services.set_community_aura(self.club, fast, by=self.root))
        self.assertEqual(CommunityAura.objects.get(community=self.club).aura, fast)
        self.assertTrue(services.set_community_aura(self.club, None, by=self.root))
        self.assertFalse(CommunityAura.objects.filter(community=self.club).exists())
        self.assertFalse(services.set_community_aura(self.club, None, by=self.root))


class SigilTests(Fixture):
    def test_a_sigil_is_its_own_thing_with_a_uuid(self):
        one = sigils.create("Lantern", 2, by=self.root, description="the first")
        two = sigils.create("Lantern", 2, by=self.root)
        self.assertNotEqual(one.uuid, two.uuid)
        self.assertEqual((one.issued_by, one.holder, one.active, one.generating),
                         (self.root, None, True, False))

    def test_assigning_hands_it_to_one_person(self):
        lantern = sigils.create("Lantern", 2, by=self.root)
        sigils.assign(lantern, self.mia, by=self.root, note="for the archive")
        lantern.refresh_from_db()
        self.assertEqual((lantern.holder, lantern.generating), (self.mia, True))
        self.assertIsNotNone(lantern.assigned_at)
        self.assertEqual(list(SigilEvent.objects.values_list("action", "user", "by", "note")),
                         [(SigilAction.ASSIGNED, self.mia.pk, self.root.pk, "for the archive")])
        sigils.assign(lantern, self.mia, by=self.root)             # again: nothing new
        self.assertEqual(SigilEvent.objects.count(), 1)

    def test_reassigning_takes_it_from_the_one_who_held_it(self):
        lantern = sigils.create("Lantern", 2, by=self.root)
        sigils.assign(lantern, self.mia, by=self.root)
        sigils.assign(lantern, self.noel, by=self.root)
        lantern.refresh_from_db()
        self.assertEqual(lantern.holder, self.noel)
        self.assertEqual(services.speed_for(self.mia).rate, 0)
        self.assertEqual(services.speed_for(self.noel).rate, D("2"))
        self.assertEqual(
            sorted(SigilEvent.objects.values_list("action", "user__username")),
            [("assigned", "mia"), ("assigned", "noel"), ("revoked", "mia")])

    def test_revoking_stops_it_and_keeps_what_it_generated(self):
        lantern = sigils.create("Lantern", 2, by=self.root)
        sigils.assign(lantern, self.mia, by=self.root)
        services.regenerate_hour()
        self.assertEqual(services.balance(self.mia), D("12"))
        sigils.revoke(lantern, by=self.root, note="left the team")
        lantern.refresh_from_db()
        self.assertEqual((lantern.holder, lantern.active, lantern.revoked_by, lantern.generating),
                         (None, False, self.root, False))
        self.assertIsNotNone(lantern.revoked_at)
        self.assertEqual(services.balance(self.mia), D("12"))
        self.assertEqual(services.speed_for(self.mia).rate, 0)
        # Still there, and may be handed out again.
        sigils.assign(lantern, self.noel, by=self.root)
        lantern.refresh_from_db()
        self.assertEqual((lantern.holder, lantern.active, lantern.revoked_at),
                         (self.noel, True, None))

    def test_erasing_the_holder_leaves_the_sigil_with_nobody(self):
        lantern = sigils.create("Lantern", 2, by=self.root)
        gone = User.objects.create_user("gone", password="pw")
        sigils.assign(lantern, gone, by=self.root)
        gone.delete()
        lantern.refresh_from_db()
        self.assertEqual((lantern.holder, lantern.generating), (None, False))
        self.assertFalse(ManaAccount.objects.filter(user_id=gone.pk).exists())
        self.assertFalse(SigilEvent.objects.filter(sigil=lantern).exists())


class RegenerationTests(Fixture):
    def setUp(self):
        aura = Aura.objects.create(name="Common", slug="common", regen_per_hour=D("4"),
                                   cap=D("15"))
        CommunityAura.objects.create(community=self.club, aura=aura)
        self.person.communities.add(self.club)

    def test_an_hour_adds_the_aura_and_the_sigils(self):
        sigils.assign(sigils.create("Lantern", D("0.5"), by=self.root), self.mia, by=self.root)
        report = services.regenerate_hour()
        self.assertEqual((report.paid, report.total), (1, D("4.5")))
        self.assertEqual(report.idle, 2)                          # root and noel: nothing regenerates
        self.assertEqual(services.balance(self.mia), D("14.5"))
        event = self.events(self.mia, ManaKind.REGEN).get()
        self.assertEqual(event.metadata["aura"], "4.0000")
        self.assertEqual(list(event.metadata["sigils"].values()), ["0.5000"])

    def test_an_hour_is_paid_once(self):
        at = timezone.now()
        services.regenerate_hour(at=at)
        again = services.regenerate_hour(at=at)
        self.assertEqual((again.paid, again.done), (0, 1))
        self.assertEqual(services.balance(self.mia), D("14"))
        self.assertEqual(self.events(self.mia, ManaKind.REGEN).count(), 1)

    def test_it_stops_at_the_cap_and_a_full_account_is_left(self):
        at = timezone.now()
        services.regenerate_hour(at=at)                                 # 14
        services.regenerate_hour(at=at + timedelta(hours=1))            # 15, not 18
        self.assertEqual(services.balance(self.mia), D("15"))
        self.assertEqual(self.events(self.mia, ManaKind.REGEN).first().delta, D("1"))
        report = services.regenerate_hour(at=at + timedelta(hours=2))
        self.assertEqual((report.paid, report.full), (0, 1))
        self.assertEqual(self.events(self.mia, ManaKind.REGEN).count(), 2)

    def test_a_granted_balance_above_the_cap_is_not_taken_down(self):
        services.grant(self.mia, 90, by=self.root)
        report = services.regenerate_hour()
        self.assertEqual(report.full, 1)
        self.assertEqual(services.balance(self.mia), D("100"))

    def test_somebody_who_is_not_active_does_not_regenerate(self):
        User.objects.filter(pk=self.mia.pk).update(is_active=False)
        self.assertEqual(services.regenerate_hour().paid, 0)
        self.assertEqual(services.balance(self.mia), D("10"))

    def test_the_task_is_the_service(self):
        from .tasks import regenerate_hour

        result = regenerate_hour()
        self.assertEqual((result["paid"], result["total"]), (1, "4.0000"))
        self.assertEqual(result["label"], services.hour_label())


class ConfigTests(Fixture):
    def problems(self, text):
        return config.validate(Path(numbers_file(text)))

    def test_the_file_of_these_tests_is_sound_and_is_what_is_read(self):
        self.assertEqual(config.validate(), [])
        numbers = config.load()
        self.assertEqual((numbers.cap, numbers.signup, numbers.default_regen_per_hour),
                         (D("100"), D("10"), D("0")))

    def test_what_a_file_may_not_say(self):
        for text, needle in (
                ("version: 2\n", "version must be 1"),
                ("version: true\n", "version must be 1"),
                ("version: 1\ncolour: red\n", "unknown top-level key"),
                ("version: 1\ncap: -5\n", "cap must be a number"),
                ("version: 1\nsignup: lots\n", "signup must be a number"),
                ("version: 1\nprices: [1]\n", "prices must be a mapping"),
                ("version: 1\nprices:\n  storage.request: -1\n", "must be a number, zero or more"),
                ("version: 1\nprices:\n  nothing.here: 1\n", "no installed app meters"),
                ("version: 1\nauras:\n  - {name: A, slug: a, regen_per_hour: 1, colour: x}\n",
                 "unknown key"),
                ("version: 1\nauras:\n  - {name: A, regen_per_hour: 1}\n", "name and slug"),
                ("version: 1\nauras:\n  - {name: A, slug: a, regen_per_hour: fast}\n",
                 "regen_per_hour must be a number"),
                ("version: 1\nauras:\n  - {name: A, slug: a, regen_per_hour: 1, communities: [x]}\n"
                 "  - {name: B, slug: b, regen_per_hour: 1, communities: [x]}\n",
                 "a community has one aura"),
                ("- 1\n", "must be a mapping")):
            with self.subTest(text=text):
                found = self.problems(text)
                self.assertTrue(any(needle in problem for problem in found), (needle, found))
        self.assertIn("no Discipline file", config.validate(Path("/nonexistent/d.yaml"))[0])

    def test_the_check_stops_a_platform_with_a_bad_file(self):
        from .checks import check_discipline

        self.assertEqual([f.id for f in check_discipline(None)], [])
        with override_settings(DISCIPLINE_FILE=numbers_file("version: 1\ncap: -5\n")):
            self.assertEqual([f.id for f in check_discipline(None)], ["discipline.E001"])

    def test_the_ingress_makes_the_files_auras_and_keeps_what_is_there(self):
        from io import StringIO

        from django.core.management import call_command

        text = NUMBERS + ("auras:\n"
                          "  - {name: Common, slug: common, regen_per_hour: 4, cap: 50, "
                          "communities: [club, nowhere]}\n")
        with override_settings(DISCIPLINE_FILE=numbers_file(text), INGRESS_MODE="realistic"):
            out = StringIO()
            call_command("ingress_discipline", stdout=out)
            aura = Aura.objects.get(slug="common")
            self.assertEqual((aura.regen_per_hour, aura.cap), (D("4"), D("50")))
            self.assertEqual(CommunityAura.objects.get(community=self.club).aura, aura)
            self.assertIn("no community nowhere", out.getvalue())
            # An administrator's numbers and choices survive the next start.
            Aura.objects.filter(pk=aura.pk).update(regen_per_hour=D("9"))
            other = Aura.objects.create(name="Other", slug="other", regen_per_hour=1)
            CommunityAura.objects.filter(community=self.club).update(aura=other)
            call_command("ingress_discipline", stdout=StringIO())
            self.assertEqual(Aura.objects.get(slug="common").regen_per_hour, D("9"))
            self.assertEqual(CommunityAura.objects.get(community=self.club).aura, other)
