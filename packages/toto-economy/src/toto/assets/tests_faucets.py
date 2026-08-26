"""Faucets: who may run one, and what an ordinary member may see.

This is what replaced the treasury payroll, and the difference is the point. The
payroll paid OFFICES — a ``socialhub.Station`` carried a stipend, so being paid
meant holding a post, and the post also carried rights and a quota multiplier.
A faucet pays PEOPLE, grants nothing, and qualifies nobody.

The hourly run itself is covered separately; these are the models, the
permissions and the page.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.assets.models import (Asset, Faucet, FaucetMember, FaucetPayout,
                                FaucetPayoutStatus)
from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform

User = get_user_model()


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
class FaucetTestCase(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        self.mana = Asset.objects.get(unit_name="MANA")
        self.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        self.ada = User.objects.create_user("ada", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")

    def make_faucet(self, name="Stipends", active=True):
        return Faucet.objects.create(name=name, asset=self.mana, active=active)


class ModelTests(FaucetTestCase):

    def test_a_faucet_is_created_switched_off_by_default(self):
        """Filled with people first, switched on second. The alternative is a
        faucet that starts paying before anybody has checked the numbers."""
        self.assertFalse(Faucet.objects.create(name="New", asset=self.mana).active)

    def test_the_slug_is_derived_and_stays_unique(self):
        first = self.make_faucet(name="Stipends")
        second = self.make_faucet(name="Stipends")
        self.assertEqual(first.slug, "stipends")
        self.assertNotEqual(second.slug, first.slug)

    def test_somebody_is_on_a_faucet_once(self):
        """Two rows would be two payouts an hour with nothing saying why."""
        faucet = self.make_faucet()
        FaucetMember.objects.create(faucet=faucet, user=self.ada,
                                    amount_per_hour=Decimal("1"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            FaucetMember.objects.create(faucet=faucet, user=self.ada,
                                        amount_per_hour=Decimal("2"))

    def test_the_same_person_may_be_on_two_faucets(self):
        """How somebody receives two currencies — one faucet drips one."""
        FaucetMember.objects.create(faucet=self.make_faucet("A"), user=self.ada,
                                    amount_per_hour=Decimal("1"))
        FaucetMember.objects.create(faucet=self.make_faucet("B"), user=self.ada,
                                    amount_per_hour=Decimal("2"))
        self.assertEqual(FaucetMember.objects.filter(user=self.ada).count(), 2)

    def test_a_negative_hourly_amount_is_refused(self):
        from django.core.exceptions import ValidationError

        member = FaucetMember(faucet=self.make_faucet(), user=self.ada,
                              amount_per_hour=Decimal("-1"))
        with self.assertRaises(ValidationError):
            member.full_clean()

    def test_one_payout_per_member_per_hour(self):
        """THE idempotency key. A beat that fires twice must not pay twice."""
        member = FaucetMember.objects.create(
            faucet=self.make_faucet(), user=self.ada, amount_per_hour=Decimal("1"))
        FaucetPayout.objects.create(member=member, period_label="hourly:2026-08-26T11")
        with self.assertRaises(IntegrityError), transaction.atomic():
            FaucetPayout.objects.create(member=member,
                                        period_label="hourly:2026-08-26T11")

    def test_the_next_hour_is_a_different_payout(self):
        member = FaucetMember.objects.create(
            faucet=self.make_faucet(), user=self.ada, amount_per_hour=Decimal("1"))
        FaucetPayout.objects.create(member=member, period_label="hourly:2026-08-26T11")
        FaucetPayout.objects.create(member=member, period_label="hourly:2026-08-26T12")
        self.assertEqual(FaucetPayout.objects.count(), 2)


class StaffManagementTests(FaucetTestCase):

    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def messages_from(self, response):
        return " ".join(str(m) for m in response.context["messages"]).lower()

    def test_staff_can_create_a_faucet(self):
        response = self.client.post(reverse("assets:faucet_create"), {
            "name": "Stipends", "asset": self.mana.pk, "note": "for the crew"},
            follow=True)
        faucet = Faucet.objects.get(name="Stipends")
        self.assertEqual(faucet.asset, self.mana)
        self.assertFalse(faucet.active)
        self.assertIn("switched off", self.messages_from(response))

    def test_a_faucet_needs_a_name_and_a_currency(self):
        before = Faucet.objects.count()
        self.assertIn("needs a name", self.messages_from(self.client.post(
            reverse("assets:faucet_create"),
            {"name": "", "asset": self.mana.pk}, follow=True)))
        self.assertIn("active currency", self.messages_from(self.client.post(
            reverse("assets:faucet_create"),
            {"name": "X", "asset": "99999"}, follow=True)))
        # Counted rather than asserted empty: bootstrap seeds the default MANA
        # and ASR faucets, so "none exist" stopped being the question. What
        # matters is that a refused create wrote nothing.
        self.assertEqual(Faucet.objects.count(), before)
        self.assertFalse(Faucet.objects.filter(name="X").exists())

    def test_staff_add_a_person_and_their_hourly_amount(self):
        faucet = self.make_faucet()
        self.client.post(reverse("assets:faucet_member_add", args=[faucet.pk]),
                         {"username": "ada", "amount_per_hour": "2.5"})
        member = FaucetMember.objects.get(faucet=faucet, user=self.ada)
        self.assertEqual(member.amount_per_hour, Decimal("2.5"))

    def test_adding_somebody_twice_updates_their_rate(self):
        """The obvious thing to do, so it must not answer "already a member"."""
        faucet = self.make_faucet()
        url = reverse("assets:faucet_member_add", args=[faucet.pk])
        self.client.post(url, {"username": "ada", "amount_per_hour": "1"})
        self.client.post(url, {"username": "ada", "amount_per_hour": "9"})
        self.assertEqual(FaucetMember.objects.filter(faucet=faucet).count(), 1)
        self.assertEqual(
            FaucetMember.objects.get(faucet=faucet).amount_per_hour, Decimal("9"))

    def test_an_unknown_account_is_refused_by_name(self):
        faucet = self.make_faucet()
        response = self.client.post(
            reverse("assets:faucet_member_add", args=[faucet.pk]),
            {"username": "nobody", "amount_per_hour": "1"}, follow=True)
        self.assertIn("no account called", self.messages_from(response))

    def test_a_nonsense_amount_is_refused(self):
        faucet = self.make_faucet()
        response = self.client.post(
            reverse("assets:faucet_member_add", args=[faucet.pk]),
            {"username": "ada", "amount_per_hour": "lots"}, follow=True)
        self.assertIn("not a number", self.messages_from(response))
        self.assertFalse(FaucetMember.objects.exists())

    def test_a_negative_amount_is_refused(self):
        faucet = self.make_faucet()
        response = self.client.post(
            reverse("assets:faucet_member_add", args=[faucet.pk]),
            {"username": "ada", "amount_per_hour": "-1"}, follow=True)
        self.assertIn("cannot be negative", self.messages_from(response))

    def test_staff_can_switch_a_faucet_on_and_off(self):
        faucet = self.make_faucet(active=False)
        self.client.post(reverse("assets:faucet_toggle", args=[faucet.pk]))
        faucet.refresh_from_db()
        self.assertTrue(faucet.active)
        self.client.post(reverse("assets:faucet_toggle", args=[faucet.pk]))
        faucet.refresh_from_db()
        self.assertFalse(faucet.active)

    def test_removing_somebody_keeps_what_they_were_already_paid(self):
        """What a faucet paid is a ledger fact. Taking somebody off the list
        must not rewrite the history of what they were paid."""
        faucet = self.make_faucet()
        member = FaucetMember.objects.create(faucet=faucet, user=self.ada,
                                             amount_per_hour=Decimal("1"))
        FaucetPayout.objects.create(member=member, period_label="hourly:2026-08-26T10",
                                    status=FaucetPayoutStatus.PAID)

        self.client.post(reverse("assets:faucet_member_remove", args=[member.pk]))

        member.refresh_from_db()
        self.assertFalse(member.active)
        self.assertEqual(FaucetPayout.objects.filter(member=member).count(), 1)


class OrdinaryUserTests(FaucetTestCase):
    """Sees their own membership and history; administers nothing."""

    def setUp(self):
        super().setUp()
        self.faucet = self.make_faucet()
        self.membership = FaucetMember.objects.create(
            faucet=self.faucet, user=self.ada, amount_per_hour=Decimal("3"))
        FaucetPayout.objects.create(member=self.membership,
                                    period_label="hourly:2026-08-26T10",
                                    amount_base_units=3_000_000_000,
                                    status=FaucetPayoutStatus.PAID)
        self.client.force_login(self.ada)

    def test_a_member_sees_their_own_faucet_and_rate(self):
        response = self.client.get(reverse("assets:faucet_list"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Stipends", body)
        self.assertIn("3", body)

    def test_a_member_sees_their_own_payout_history(self):
        body = self.client.get(reverse("assets:faucet_list")).content.decode()
        self.assertIn("hourly:2026-08-26T10", body)

    def test_a_member_is_offered_no_controls(self):
        response = self.client.get(reverse("assets:faucet_list"))
        self.assertFalse(response.context["can_manage"])
        body = response.content.decode()
        self.assertNotIn(reverse("assets:faucet_create"), body)

    def test_a_member_sees_nobody_else_s_payouts(self):
        other = FaucetMember.objects.create(faucet=self.faucet, user=self.bob,
                                            amount_per_hour=Decimal("99"))
        FaucetPayout.objects.create(member=other,
                                    period_label="hourly:2026-08-26T10",
                                    status=FaucetPayoutStatus.PAID)
        response = self.client.get(reverse("assets:faucet_list"))
        self.assertEqual(list(response.context["my_payouts"]),
                         list(FaucetPayout.objects.filter(member=self.membership)))

    def test_a_member_may_not_create_a_faucet(self):
        before = Faucet.objects.count()
        response = self.client.post(reverse("assets:faucet_create"),
                                    {"name": "Mine", "asset": self.mana.pk})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Faucet.objects.count(), before)
        self.assertFalse(Faucet.objects.filter(name="Mine").exists())

    def test_a_member_may_not_add_themselves_to_one(self):
        response = self.client.post(
            reverse("assets:faucet_member_add", args=[self.faucet.pk]),
            {"username": "ada", "amount_per_hour": "9999"})
        self.assertEqual(response.status_code, 403)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.amount_per_hour, Decimal("3"))

    def test_a_member_may_not_switch_a_faucet_on(self):
        off = self.make_faucet(name="Off", active=False)
        response = self.client.post(reverse("assets:faucet_toggle", args=[off.pk]))
        self.assertEqual(response.status_code, 403)
        off.refresh_from_db()
        self.assertFalse(off.active)

    def test_a_member_may_not_remove_anybody(self):
        response = self.client.post(
            reverse("assets:faucet_member_remove", args=[self.membership.pk]))
        self.assertEqual(response.status_code, 403)
        self.membership.refresh_from_db()
        self.assertTrue(self.membership.active)

    def test_every_managing_route_refuses_a_get(self):
        """They write, so the subscription gate has to be able to see them."""
        for route, args in (("assets:faucet_create", []),
                            ("assets:faucet_toggle", [self.faucet.pk]),
                            ("assets:faucet_member_add", [self.faucet.pk]),
                            ("assets:faucet_member_remove", [self.membership.pk])):
            with self.subTest(route=route):
                self.assertEqual(
                    self.client.get(reverse(route, args=args)).status_code, 405)
