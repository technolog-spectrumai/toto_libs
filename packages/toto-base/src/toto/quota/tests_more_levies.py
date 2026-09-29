"""``toto.quota.levies`` — the levies that drain the mana pools, read and armed
from the limits side: which metrics are levies, a rule read as plain data, the
refusal to arm a levy that would charge nothing, the banner for one already
armed that way, a member's position tonight, and the freeze that must never
raise. Skipped where the economy (and so the levy engine) is not installed.
"""

import shutil
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.quota import levies, rates

User = get_user_model()

PLAIN = "security.plain_gb_day"
STORED = "storage.gb_day"


def without(app_label):
    real = apps.is_installed
    return mock.patch.object(apps, "is_installed",
                             side_effect=lambda name: name != app_label and real(name))


class LevyTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        if not all(apps.is_installed(a) for a in ("toto.tax", "toto.mana", "toto.vault")):
            raise unittest.SkipTest("no levy engine, pools or vault on this host")
        from toto.assets.testing import TEST_ISSUER_KEY

        media = tempfile.mkdtemp(prefix="quota-levies-")
        cls.addClassCleanup(shutil.rmtree, media, ignore_errors=True)
        settings = override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY,
                                     ASSETS_MONETARY_MASTER=True, MEDIA_ROOT=media)
        settings.enable()
        cls.addClassCleanup(settings.disable)
        super().setUpClass()

    def setUp(self):
        from toto.mana.tests.fixtures import economy, seed_prices

        economy()
        seed_prices()
        self.ada = User.objects.create_user("ada", password="pw")

    def rule(self, code=PLAIN, **fields):
        from toto.tax.models import TaxRule

        rule, _ = TaxRule.objects.get_or_create(metric_code=code)
        for name, value in fields.items():
            setattr(rule, name, value)
        rule.save()
        return rule


class LevyReadTests(LevyTestCase):
    def test_the_pool_drains_are_levies_and_an_action_is_not(self):
        self.assertTrue({PLAIN, STORED} <= levies.levy_codes())
        self.assertNotIn("storage.request", levies.levy_codes())
        self.assertIsNone(levies.of("storage.request"))

    def test_a_levy_with_no_rule_says_so(self):
        from toto.tax.models import TaxRule

        TaxRule.objects.filter(metric_code=PLAIN).delete()
        row = levies.of(PLAIN)
        self.assertEqual((row["metric_code"], row["has_rule"], row["active"]), (PLAIN, False, None))
        self.assertEqual(row["raw_per_unit"], 2 ** 30)

    def test_a_rule_is_read_as_plain_data(self):
        self.rule(active=False, unit_label="GB-day")
        row = levies.of(PLAIN)
        self.assertEqual((row["has_rule"], row["active"], row["unit_label"]), (True, False, "GB-day"))
        self.assertTrue(all(isinstance(v, (str, int, bool, type(None))) for v in row.values()))

    def test_an_unreadable_rule_table_reads_as_no_rule(self):
        from toto.tax.models import TaxRule

        with mock.patch.object(TaxRule.objects, "filter", side_effect=DatabaseError("migrating")):
            row = levies.of(PLAIN)
        self.assertEqual((row["has_rule"], row["active"]), (False, None))

    def test_no_engine_leaves_active_unknown_rather_than_false(self):
        self.rule(active=True)
        with without("toto.tax"):
            row = levies.of(PLAIN)
        self.assertEqual((row["has_rule"], row["active"]), (False, None))


class ArmingTests(LevyTestCase):
    def test_a_per_action_metric_cannot_be_armed(self):
        self.assertFalse(levies.set_armed("storage.request", True))

    def test_an_unpriced_levy_is_refused_and_stays_unarmed(self):
        self.rule(active=False)
        rates.clear_price(PLAIN)
        with self.assertRaises(levies.UnpricedLevy):
            levies.set_armed(PLAIN, True)
        self.assertFalse(levies.of(PLAIN)["active"])

    def test_a_priced_levy_is_armed(self):
        self.rule(active=False)
        self.assertTrue(levies.set_armed(PLAIN, True))
        self.assertTrue(levies.of(PLAIN)["active"])

    def test_disarming_needs_no_price(self):
        self.rule(active=True)
        rates.clear_price(PLAIN)
        self.assertTrue(levies.set_armed(PLAIN, False))
        self.assertFalse(levies.of(PLAIN)["active"])

    def test_no_engine_arms_nothing(self):
        with without("toto.tax"):
            self.assertFalse(levies.set_armed(PLAIN, True))


class UnpricedBannerTests(LevyTestCase):
    def test_an_armed_levy_that_lost_its_price_is_named(self):
        self.rule(active=True)
        rates.clear_price(PLAIN)
        self.assertIn(PLAIN, levies.unpriced_levies())

    def test_a_disarmed_or_priced_levy_is_not(self):
        self.rule(active=True)
        self.rule(STORED, active=False)
        rates.clear_price(STORED)
        found = levies.unpriced_levies()
        self.assertNotIn(PLAIN, found)                    # armed and priced
        self.assertNotIn(STORED, found)                   # unpriced but disarmed

    def test_no_engine_no_banner(self):
        with without("toto.tax"):
            self.assertEqual(levies.unpriced_levies(), [])


class MyLevyTests(LevyTestCase):
    def setUp(self):
        super().setUp()
        from toto.tax.tests.factories import GB, make_vault_file

        self.rule(active=True, unit_label="GB-day")
        make_vault_file(self.ada, 5 * GB)

    def test_a_member_is_told_what_they_hold_and_what_tonight_costs(self):
        position = levies.my_levy(PLAIN, self.ada)
        self.assertEqual((position["measured"], position["billable"]), (Decimal(5), Decimal(5)))
        self.assertEqual(position["estimate"]["amount"], Decimal(100))
        self.assertEqual(position["unit_label"], "GB-day")
        self.assertIsNone(position["arrears"])

    def test_a_disarmed_levy_has_no_position(self):
        self.rule(active=False)
        self.assertIsNone(levies.my_levy(PLAIN, self.ada))

    def test_nobody_signed_in_has_no_position(self):
        self.assertIsNone(levies.my_levy(PLAIN, AnonymousUser()))
        self.assertIsNone(levies.my_levy(PLAIN, None))

    def test_a_failing_estimate_never_breaks_the_page(self):
        with mock.patch("toto.tax.services.estimate_for_user", side_effect=RuntimeError("boom")):
            self.assertIsNone(levies.my_levy(PLAIN, self.ada))

    def test_an_open_case_is_flattened_to_plain_data(self):
        from toto.tax.models import ArrearsStatus, TaxArrearsCase

        deadline = timezone.now() + timedelta(days=3)
        TaxArrearsCase.objects.create(user=self.ada, rule=self.rule(), status=ArrearsStatus.WARNED,
                                      deadline_at=deadline)
        self.assertEqual(levies.my_levy(PLAIN, self.ada)["arrears"],
                         {"status": "warned", "deadline_at": deadline})

    @unittest.skip("SUSPECTED BUG toto/tax/services.py:397-405 (estimate_for_user, read by "
                   "quota.levies.my_levy): tonight's estimate is priced at the LIST price, but "
                   "the levy is charged through quota.charge and so takes the member's "
                   "community discount — a 50% member is quoted 100 and charged 50")
    def test_a_discounted_member_is_quoted_what_the_levy_will_take(self):
        from toto.people.models import Person
        from toto.socialhub.models import Community
        from toto.subscriptions.models import CommunityDiscount

        students = Community.objects.create(name="Students")
        CommunityDiscount.objects.create(community=students, percent=50)
        Person.objects.create(user=self.ada, display_name="ada").communities.add(students)
        self.assertEqual(levies.my_levy(PLAIN, self.ada)["estimate"]["amount"], Decimal(50))


class FreezeTests(LevyTestCase):
    def test_a_warned_case_past_its_deadline_freezes(self):
        from toto.tax.models import ArrearsStatus, TaxArrearsCase

        TaxArrearsCase.objects.create(user=self.ada, rule=self.rule(), status=ArrearsStatus.WARNED,
                                      deadline_at=timezone.now() - timedelta(minutes=1))
        self.assertTrue(levies.user_is_frozen(self.ada))

    def test_a_case_still_inside_its_deadline_does_not(self):
        from toto.tax.models import ArrearsStatus, TaxArrearsCase

        TaxArrearsCase.objects.create(user=self.ada, rule=self.rule(), status=ArrearsStatus.WARNED,
                                      deadline_at=timezone.now() + timedelta(days=1))
        self.assertFalse(levies.user_is_frozen(self.ada))

    def test_an_unreadable_arrears_table_is_never_a_refusal(self):
        with mock.patch("toto.tax.arrears.is_frozen", side_effect=DatabaseError("locked")):
            self.assertFalse(levies.user_is_frozen(self.ada))

    def test_no_engine_freezes_nobody(self):
        with without("toto.tax"):
            self.assertFalse(levies.user_is_frozen(self.ada))
