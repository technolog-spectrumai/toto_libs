"""Scratch probe — clock lens. Not part of the suite."""
import datetime as dt
import zoneinfo
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.assets.models import (Asset, Faucet, FaucetMember, FaucetPayout,
                                FaucetPayoutStatus, FaucetRun, LedgerTransaction)
from toto.assets.services import faucets
from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform

User = get_user_model()


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
class ClockProbe(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        self.mana = Asset.objects.get(unit_name="MANA")
        self.ada = User.objects.create_user("ada", password="pw")
        self.faucet = Faucet.objects.create(name="Stipends", asset=self.mana,
                                            active=True)

    def add(self, user, amount="1", **over):
        return FaucetMember.objects.create(
            faucet=self.faucet, user=user, amount_per_hour=Decimal(amount), **over)

    def txs(self):
        return LedgerTransaction.objects.filter(reference__startswith="faucet-")

    # ---- 1. naive `at` -----------------------------------------------------
    def test_naive_at_uses_os_localtime_not_django_tz(self):
        aware = dt.datetime(2026, 8, 26, 11, 30, tzinfo=dt.timezone.utc)
        naive = dt.datetime(2026, 8, 26, 11, 30)   # what Django calls 11:30 (TIME_ZONE=UTC)
        print("AWARE:", faucets.period_label(aware), "NAIVE:", faucets.period_label(naive))

    # ---- 2. USE_TZ False ---------------------------------------------------
    @override_settings(USE_TZ=False)
    def test_use_tz_false(self):
        print("USE_TZ=False now():", timezone.now(), "label:", faucets.period_label())

    # ---- 3. DST repeat / gap ----------------------------------------------
    def test_dst_repeated_hour(self):
        wz = zoneinfo.ZoneInfo("Europe/Warsaw")
        a = dt.datetime(2026, 10, 25, 2, 7, tzinfo=wz, fold=0)
        b = dt.datetime(2026, 10, 25, 2, 7, tzinfo=wz, fold=1)
        print("fold0:", faucets.period_label(a), "fold1:", faucets.period_label(b))
        gap = dt.datetime(2026, 3, 29, 2, 7, tzinfo=wz)   # does not exist locally
        print("gap:", faucets.period_label(gap))

    # ---- 4. hour boundary --------------------------------------------------
    def test_two_runs_two_seconds_apart_pay_twice(self):
        self.add(self.ada, "2")
        t = dt.datetime(2026, 8, 26, 10, 59, 59, tzinfo=dt.timezone.utc)
        r1 = faucets.run_hour(at=t)
        r2 = faucets.run_hour(at=t + dt.timedelta(seconds=2))
        print("boundary:", r1, "|", r2, "| txs:", self.txs().count())

    # ---- 5. celery crontab over a simulated day ---------------------------
    def test_crontab_fire_pattern(self):
        from celery.schedules import crontab
        from toto.schedules import beat_schedule
        sched = beat_schedule(faucets=True)["faucet-hourly-payout"]["schedule"]
        print("crontab:", repr(sched), "tz:", sched.tz)
