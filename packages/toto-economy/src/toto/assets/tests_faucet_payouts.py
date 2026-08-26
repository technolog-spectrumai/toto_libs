"""The hourly sweep: paying once, and never twice.

This is the platform's only recurring outbound payment since the treasury
payroll left with Stations, and the property that matters is the one the payroll
also had: **a beat that fires twice, a worker that overlaps another, and an
operator re-running an hour by hand must all pay each person exactly once.**

The guard is not a lock. ``FaucetPayout`` carries a unique
``(member, period_label)`` and every payout's ledger reference is unique too, so
the collision happens in the DATABASE — a lock held in Python is a lock one
crashed worker holds forever.

Every test here asserts on the LEDGER as well as on the payout rows, because a
payout row saying PAID with no transaction behind it is the exact failure this
design is shaped to prevent.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.assets.models import (Asset, Faucet, FaucetMember, FaucetPayout,
                                FaucetPayoutStatus, FaucetRun, LedgerTransaction)
from toto.assets.services import faucets
from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform

User = get_user_model()


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
class PayoutTestCase(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        self.mana = Asset.objects.get(unit_name="MANA")
        self.ada = User.objects.create_user("ada", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")
        self.faucet = Faucet.objects.create(name="Stipends", asset=self.mana,
                                            active=True)

    def add(self, user, amount="1", **over):
        return FaucetMember.objects.create(
            faucet=self.faucet, user=user, amount_per_hour=Decimal(amount), **over)

    def paid_transactions(self):
        return LedgerTransaction.objects.filter(reference__startswith="faucet-")


class OneHourTests(PayoutTestCase):

    def test_a_run_pays_every_due_member(self):
        self.add(self.ada, "2")
        self.add(self.bob, "3")
        report = faucets.run_hour()
        self.assertEqual(report.paid, 2)
        self.assertEqual(FaucetPayout.objects.count(), 2)

    def test_every_payout_is_an_ordinary_asset_transaction(self):
        """No faucet-shaped ledger entry — it shows up wherever any transfer
        does, which is what the Assets UI renders."""
        self.add(self.ada, "2")
        faucets.run_hour()
        payout = FaucetPayout.objects.get()
        self.assertEqual(payout.status, FaucetPayoutStatus.PAID)
        self.assertIsNotNone(payout.transaction_id)
        self.assertEqual(self.paid_transactions().count(), 1)

    def test_the_amount_is_recorded_in_base_units_as_paid(self):
        """Denormalised on purpose: a rate that changes on Tuesday must not
        rewrite what Monday says it paid."""
        member = self.add(self.ada, "2")
        faucets.run_hour()
        payout = FaucetPayout.objects.get()
        self.assertEqual(payout.amount_base_units,
                         2 * 10 ** self.mana.decimals)

        member.amount_per_hour = Decimal("99")
        member.save(update_fields=["amount_per_hour"])
        payout.refresh_from_db()
        self.assertEqual(payout.amount_base_units, 2 * 10 ** self.mana.decimals)


class IdempotencyTests(PayoutTestCase):
    """The property the whole design is shaped around."""

    def test_running_the_same_hour_twice_pays_once(self):
        self.add(self.ada, "2")
        first = faucets.run_hour()
        second = faucets.run_hour()

        self.assertEqual((first.paid, first.skipped), (1, 0))
        self.assertEqual((second.paid, second.skipped), (0, 1))
        self.assertEqual(FaucetPayout.objects.count(), 1)
        self.assertEqual(self.paid_transactions().count(), 1)

    def test_a_retry_moves_no_money(self):
        """Asserted on the ledger, because that is the thing that must not
        double — a payout row is only a claim about it."""
        self.add(self.ada, "2")
        faucets.run_hour()
        before = self.paid_transactions().count()
        for _ in range(5):
            faucets.run_hour()
        self.assertEqual(self.paid_transactions().count(), before)

    def test_the_next_hour_pays_again(self):
        """Idempotent per HOUR, not forever — otherwise a faucet pays once and
        stops, which would look identical from the payout table."""
        self.add(self.ada, "2")
        now = timezone.now()
        faucets.run_hour(at=now)
        faucets.run_hour(at=now + timezone.timedelta(hours=1))
        self.assertEqual(FaucetPayout.objects.count(), 2)
        self.assertEqual(self.paid_transactions().count(), 2)

    def test_two_overlapping_workers_pay_once_between_them(self):
        """The collision is in the database, not in a lock: whichever loses the
        unique constraint reports "skipped" and moves on."""
        self.add(self.ada, "2")
        label = faucets.period_label()
        member = FaucetMember.objects.get()

        first = faucets.pay_member(member, label)
        second = faucets.pay_member(member, label)

        self.assertEqual({first, second}, {"paid", "skipped"})
        self.assertEqual(FaucetPayout.objects.count(), 1)
        self.assertEqual(self.paid_transactions().count(), 1)

    def test_the_hour_label_is_utc_and_hour_aligned(self):
        """A server that changes timezone must not produce two labels for one
        hour — the label is compared against stored ones."""
        moment = timezone.now().replace(minute=3, second=0, microsecond=0)
        self.assertEqual(faucets.period_label(moment),
                         faucets.period_label(moment.replace(minute=59)))


class SafetyTests(PayoutTestCase):
    """Disabled faucets, removed members, and one failure among many."""

    def test_a_switched_off_faucet_pays_nobody(self):
        self.add(self.ada, "2")
        self.faucet.active = False
        self.faucet.save(update_fields=["active"])
        self.assertEqual(faucets.run_hour().paid, 0)
        self.assertFalse(FaucetPayout.objects.exists())

    def test_a_removed_member_is_not_paid(self):
        member = self.add(self.ada, "2")
        member.active = False
        member.save(update_fields=["active"])
        self.assertEqual(faucets.run_hour().paid, 0)

    def test_a_retired_currency_stops_its_faucet(self):
        self.add(self.ada, "2")
        self.mana.active = False
        self.mana.save(update_fields=["active"])
        self.assertEqual(faucets.run_hour().paid, 0)

    def test_a_member_on_zero_is_recorded_rather_than_skipped(self):
        """On the list, currently paid nothing. A gap in the history would look
        like a missed run."""
        self.add(self.ada, "0")
        report = faucets.run_hour()
        self.assertEqual(report.paid, 1)
        payout = FaucetPayout.objects.get()
        self.assertEqual(payout.status, FaucetPayoutStatus.PAID)
        self.assertIsNone(payout.transaction_id)
        self.assertEqual(self.paid_transactions().count(), 0)

    def test_one_failure_does_not_stop_anybody_else_being_paid(self):
        """THE isolation property. A dry reserve for one member must not be a
        dry reserve for the run."""
        self.add(self.ada, "2")
        broken_asset = Asset.objects.get(unit_name="ASR")
        broken_asset.reserve_account = None
        broken_asset.save(update_fields=["reserve_account"])
        broken = Faucet.objects.create(name="Broken", asset=broken_asset,
                                       active=True)
        FaucetMember.objects.create(faucet=broken, user=self.bob,
                                    amount_per_hour=Decimal("5"))

        report = faucets.run_hour()

        self.assertEqual(report.paid, 1)
        self.assertEqual(report.failed, 1)
        self.assertEqual(
            FaucetPayout.objects.get(member__user=self.ada).status,
            FaucetPayoutStatus.PAID)
        self.assertEqual(
            FaucetPayout.objects.get(member__user=self.bob).status,
            FaucetPayoutStatus.FAILED)

    def test_a_failure_records_why_in_words(self):
        """"failed" with nothing beside it is a state an operator cannot act on."""
        broken_asset = Asset.objects.get(unit_name="ASR")
        broken_asset.reserve_account = None
        broken_asset.save(update_fields=["reserve_account"])
        broken = Faucet.objects.create(name="Broken", asset=broken_asset,
                                       active=True)
        FaucetMember.objects.create(faucet=broken, user=self.bob,
                                    amount_per_hour=Decimal("5"))

        faucets.run_hour()
        self.assertIn("reserve", FaucetPayout.objects.get().detail.lower())

    def test_a_failed_payout_is_not_retried_into_a_double_payment(self):
        """A failed hour stays failed. Re-running claims the hour again and
        finds it taken, rather than paying it a second time once the cause is
        fixed — a faucet drips, it does not backfill."""
        broken_asset = Asset.objects.get(unit_name="ASR")
        broken_asset.reserve_account = None
        broken_asset.save(update_fields=["reserve_account"])
        broken = Faucet.objects.create(name="Broken", asset=broken_asset,
                                       active=True)
        FaucetMember.objects.create(faucet=broken, user=self.bob,
                                    amount_per_hour=Decimal("5"))

        faucets.run_hour()
        broken_asset.reserve_account = self.mana.reserve_account
        broken_asset.save(update_fields=["reserve_account"])
        report = faucets.run_hour()

        self.assertEqual(report.skipped, 1)
        self.assertEqual(FaucetPayout.objects.count(), 1)


class AuditTests(PayoutTestCase):
    """Each EXECUTION is recorded, which is how a retry proves itself."""

    def test_a_run_is_recorded(self):
        self.add(self.ada, "2")
        faucets.run_hour()
        run = FaucetRun.objects.get()
        self.assertEqual((run.paid, run.skipped, run.failed), (1, 0, 0))
        self.assertIsNotNone(run.finished_at)

    def test_a_retry_leaves_its_own_row_showing_it_paid_nobody(self):
        """Silence could equally mean nothing ran."""
        self.add(self.ada, "2")
        faucets.run_hour()
        faucets.run_hour()
        runs = list(FaucetRun.objects.order_by("id"))
        self.assertEqual(len(runs), 2)
        self.assertEqual((runs[0].paid, runs[0].skipped), (1, 0))
        self.assertEqual((runs[1].paid, runs[1].skipped), (0, 1))

    def test_failures_are_named_on_the_run_as_well(self):
        broken_asset = Asset.objects.get(unit_name="ASR")
        broken_asset.reserve_account = None
        broken_asset.save(update_fields=["reserve_account"])
        broken = Faucet.objects.create(name="Broken", asset=broken_asset,
                                       active=True)
        FaucetMember.objects.create(faucet=broken, user=self.bob,
                                    amount_per_hour=Decimal("5"))
        faucets.run_hour()
        self.assertIn("bob", FaucetRun.objects.get().detail)


class TaskTests(PayoutTestCase):
    """The Celery wrapper, which must add nothing of its own."""

    def test_the_task_pays_and_reports(self):
        from toto.assets.tasks import run_faucet_hour

        self.add(self.ada, "2")
        result = run_faucet_hour()
        self.assertEqual(result["paid"], 1)
        self.assertEqual(FaucetPayout.objects.count(), 1)

    def test_firing_the_task_twice_pays_once(self):
        """What a beat actually does when it misfires."""
        from toto.assets.tasks import run_faucet_hour

        self.add(self.ada, "2")
        run_faucet_hour()
        second = run_faucet_hour()
        self.assertEqual(second["paid"], 0)
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(self.paid_transactions().count(), 1)


class BeatWiringTests(TestCase):
    """The schedule entry, and the flag that decides whether it exists."""

    def test_the_entry_is_hourly_when_switched_on(self):
        from toto.schedules import beat_schedule

        entry = beat_schedule(faucets=True)["faucet-hourly-payout"]
        self.assertEqual(entry["task"], "toto.assets.tasks.run_faucet_hour")
        # Every hour, once. Celery normalises the crontab's `*` into the full
        # set, so the assertion is on the set rather than on the string: all
        # 24 hours, and exactly one minute within each.
        schedule = entry["schedule"]
        self.assertEqual(len(schedule.hour), 24)
        self.assertEqual(len(schedule.minute), 1)

    def test_the_step_is_an_hour_and_not_a_setting(self):
        """Every amount is denominated per hour, so a run covering anything
        else would be paying a rate nobody typed. `faucets_minute` moves it
        WITHIN the hour; there is no knob that changes the period."""
        from toto.schedules import beat_schedule

        for minute in (0, 7, 45):
            with self.subTest(minute=minute):
                schedule = beat_schedule(
                    faucets=True, faucets_minute=minute)["faucet-hourly-payout"]["schedule"]
                self.assertEqual(schedule.minute, {minute})
                self.assertEqual(len(schedule.hour), 24)

    def test_there_is_no_entry_when_switched_off(self):
        from toto.schedules import beat_schedule

        self.assertNotIn("faucet-hourly-payout", beat_schedule(faucets=False))

    def test_the_task_module_is_discoverable(self):
        """Autodiscovery imports `<label>.tasks` and nothing else, and the label
        must be in TASK_MODULES — a task the worker cannot find is a beat
        enqueuing into a KeyError."""
        from toto.registry import TASK_MODULES

        self.assertIn("toto.assets", TASK_MODULES)


class CrashRecoveryTests(PayoutTestCase):
    """The one gap the two-layer idempotency leaves, and how it heals.

    `pay_member` claims the hour, transfers, then records the transaction. A
    process that dies between the second and third steps leaves a payout saying
    PENDING with a real transfer behind it: nobody is paid twice, because the
    claim survives and the next run skips — but the audit trail says something
    false, and "PENDING forever" reads the same as "never attempted".
    """

    def simulate_crash_after_transfer(self, member, label):
        """Exactly the state a mid-payment crash leaves: the claim and the
        money, without the record joining them."""
        from toto.assets.services.assets import distribute_asset

        FaucetPayout.objects.create(
            member=member, period_label=label,
            amount_base_units=int(member.amount_per_hour * 10 ** self.mana.decimals),
            status=FaucetPayoutStatus.PENDING)
        return distribute_asset(
            asset=self.mana, recipient_account=self._account(member.user),
            amount=member.amount_per_hour,
            reference=faucets.reference_for(member, label))

    def _account(self, user):
        from toto.assets.prepaid import get_or_create_prepaid_account

        account, _ = get_or_create_prepaid_account(user)
        return account

    def test_a_crashed_payment_is_never_paid_twice(self):
        """The property that must hold even before any repair."""
        member = self.add(self.ada, "2")
        label = faucets.period_label()
        self.simulate_crash_after_transfer(member, label)

        faucets.run_hour()

        self.assertEqual(self.paid_transactions().count(), 1)
        self.assertEqual(FaucetPayout.objects.count(), 1)

    def test_the_next_run_reconciles_the_row_from_the_ledger(self):
        """Reading the LEDGER rather than trusting the row — the same rule the
        fee board follows, because the ledger is what actually moved."""
        member = self.add(self.ada, "2")
        label = faucets.period_label()
        tx = self.simulate_crash_after_transfer(member, label)

        faucets.run_hour()

        payout = FaucetPayout.objects.get()
        self.assertEqual(payout.status, FaucetPayoutStatus.PAID)
        self.assertEqual(payout.transaction_id, tx.pk)
        self.assertIn("reconciled", payout.detail)

    def test_a_genuinely_unpaid_pending_row_is_left_alone(self):
        """No transfer behind it means it is not paid, and inventing a PAID
        status would be the lie this repair exists to remove."""
        member = self.add(self.ada, "2")
        label = faucets.period_label()
        FaucetPayout.objects.create(member=member, period_label=label,
                                    status=FaucetPayoutStatus.PENDING)

        faucets.run_hour()

        payout = FaucetPayout.objects.get()
        self.assertEqual(payout.status, FaucetPayoutStatus.PENDING)
        self.assertIsNone(payout.transaction_id)
        self.assertEqual(self.paid_transactions().count(), 0)

    def test_reconciling_does_not_move_money(self):
        member = self.add(self.ada, "2")
        label = faucets.period_label()
        self.simulate_crash_after_transfer(member, label)
        before = self.paid_transactions().count()

        faucets.run_hour()
        faucets.run_hour()

        self.assertEqual(self.paid_transactions().count(), before)

    def test_a_failed_payout_is_not_reconciled_into_paid(self):
        """FAILED means the transfer raised, so there is nothing to find — and
        the repair must only ever touch PENDING."""
        member = self.add(self.ada, "2")
        label = faucets.period_label()
        FaucetPayout.objects.create(member=member, period_label=label,
                                    status=FaucetPayoutStatus.FAILED,
                                    detail="reserve empty")

        faucets.run_hour()

        payout = FaucetPayout.objects.get()
        self.assertEqual(payout.status, FaucetPayoutStatus.FAILED)
        self.assertEqual(payout.detail, "reserve empty")


class UnpayableRateTests(PayoutTestCase):
    """A rate too large for the ledger to record.

    `amount_per_hour` is numeric(30,18) and holds about 1e12; a payout's base
    units are a signed 64-bit integer, so for a 9-decimal currency every rate
    above ~9.22 billion is storable and unpayable. The obvious way to land in
    that gap is pasting a BASE-UNIT figure into a field that wants display
    units.

    It used to take the whole sweep down: the claim INSERT raised DataError on
    Postgres (OverflowError on sqlite), neither of which is an IntegrityError,
    so it escaped `pay_member`, escaped `run_hour`, and every member ordered
    after the offender went unpaid — that hour and every hour after, because the
    condition was deterministic and the beat retried straight into it.
    """

    HUGE = "10000000000"          # ten billion MANA/hour; MANA has 9 decimals

    def test_the_form_refuses_it_and_says_why(self):
        staff = User.objects.create_user("staff", password="pw", is_staff=True)
        self.client.force_login(staff)
        response = self.client.post(
            reverse("assets:faucet_member_add", args=[self.faucet.pk]),
            {"username": "ada", "amount_per_hour": self.HUGE}, follow=True)
        text = " ".join(str(m) for m in response.context["messages"]).lower()
        self.assertIn("more mana an hour than the ledger can record", text)
        self.assertFalse(FaucetMember.objects.exists())

    def test_the_model_refuses_it(self):
        from django.core.exceptions import ValidationError

        member = FaucetMember(faucet=self.faucet, user=self.ada,
                              amount_per_hour=Decimal(self.HUGE))
        with self.assertRaises(ValidationError):
            member.full_clean()

    def test_an_ordinary_large_rate_is_still_allowed(self):
        """The ceiling is the column's, not an opinion about generosity."""
        member = FaucetMember(faucet=self.faucet, user=self.ada,
                              amount_per_hour=Decimal("1000000"))
        self.assertEqual(member.rate_problem(), "")

    def test_one_unpayable_rate_does_not_stop_the_sweep(self):
        """THE bug. Written straight to the database, bypassing both guards, so
        this asserts the sweep survives a row however it got there."""
        FaucetMember.objects.create(faucet=self.faucet, user=self.ada,
                                    amount_per_hour=Decimal("2"))
        FaucetMember.objects.create(faucet=self.faucet, user=self.bob,
                                    amount_per_hour=Decimal(self.HUGE))
        third = User.objects.create_user("zoe", password="pw")
        FaucetMember.objects.create(faucet=self.faucet, user=third,
                                    amount_per_hour=Decimal("3"))

        report = faucets.run_hour()          # must not raise

        self.assertEqual(report.failed, 1)
        self.assertEqual(report.paid, 2)
        for who in (self.ada, third):
            with self.subTest(user=who.username):
                self.assertEqual(
                    FaucetPayout.objects.get(member__user=who).status,
                    FaucetPayoutStatus.PAID)

    def test_the_task_survives_it_too(self):
        """The beat must not lose the hour."""
        from toto.assets.tasks import run_faucet_hour

        FaucetMember.objects.create(faucet=self.faucet, user=self.bob,
                                    amount_per_hour=Decimal(self.HUGE))
        FaucetMember.objects.create(faucet=self.faucet, user=self.ada,
                                    amount_per_hour=Decimal("2"))
        result = run_faucet_hour()
        self.assertEqual(result["paid"], 1)
        self.assertEqual(result["failed"], 1)

    def test_the_run_row_is_written_even_when_a_member_blows_up(self):
        """An audit table asserting nobody was paid while the ledger says
        otherwise is worse than no audit table."""
        FaucetMember.objects.create(faucet=self.faucet, user=self.ada,
                                    amount_per_hour=Decimal("2"))
        FaucetMember.objects.create(faucet=self.faucet, user=self.bob,
                                    amount_per_hour=Decimal(self.HUGE))

        faucets.run_hour()

        run = FaucetRun.objects.get()
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(run.paid, 1)
        self.assertEqual(run.failed, 1)
        self.assertIn("bob", run.detail)
