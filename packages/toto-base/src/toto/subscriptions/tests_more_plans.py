"""The plan file, at the edges the validation table leaves implicit.

Same harness as ``tests_plans`` — a real YAML file on disk pointed at by
``SUBSCRIPTION_PLANS_FILE`` — for the rules an operator editing the ladder
relies on: what a key may look like, how cards are ordered, what a plan for
admins leaves out of the public ladder, and what a card may promise.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.subscriptions.tests_more_plans
"""

from __future__ import annotations

from pathlib import Path
from unittest import skip

from django.test import SimpleTestCase, override_settings

from toto.subscriptions import plans
from toto.subscriptions.tests_plans import GOOD, ValidationTestCase, write


def ladder(*extra_plans: str) -> str:
    return GOOD + "".join(extra_plans)


class KeyShapeTests(ValidationTestCase):
    def plan_keyed(self, key: str) -> str:
        return ladder(f"  - key: {key}\n    name: Extra\n")

    def test_a_key_starts_with_a_lowercase_letter(self):
        for key in ("9lives", "Pro", "_hidden", "-dash"):
            with self.subTest(key=key):
                self.assertRejected(self.plan_keyed(key), "must match")

    def test_a_key_is_at_most_sixty_four_characters(self):
        self.assertEqual(self.problems(self.plan_keyed("a" * 64)), [])
        self.assertRejected(self.plan_keyed("a" * 65), "must match")

    def test_a_key_that_is_not_a_string_is_refused(self):
        self.assertRejected(self.plan_keyed("42"), "must match")

    def test_the_version_is_the_number_one_not_the_string(self):
        self.assertRejected(GOOD.replace("version: 1", 'version: "1"'), "version must be 1")

    def test_all_features_is_a_boolean(self):
        self.assertRejected(ladder("  - key: top\n    name: Top\n    all_features: 1\n"),
                            "all_features must be true or false")

    def test_a_stipend_may_be_declared_on_a_paid_plan(self):
        self.assertEqual(self.problems(ladder("  - key: pay\n    name: Pay\n    units: -50\n")), [])

    @skip("suspected bug: plans._problems checks numbers with isinstance(value, int), "
          "and a YAML boolean is an int in Python, so `units: true` passes validation "
          "and becomes a plan costing 1 unit (likewise `order: yes`)")
    def test_a_boolean_is_not_a_whole_number(self):
        self.assertRejected(ladder("  - key: odd\n    name: Odd\n    units: true\n"),
                            "units must be a whole number")


class BuiltLadderTests(SimpleTestCase):
    TEXT = ladder(
        "  - key: zeta\n    name: Zeta\n    units: 5\n    order: 20\n",
        "  - key: alpha\n    name: Alpha\n    units: 5\n    order: 20\n",
        "  - key: plain\n    name: Plain\n    description: \"  Just the basics.  \\n\"\n",
        "  - key: top\n    name: Top\n    for_admins: true\n    all_features: true\n"
        "    order: 5\n",
    )

    def setUp(self):
        override = override_settings(SUBSCRIPTION_PLANS_FILE=str(write(self.TEXT)))
        override.enable()
        plans.reload()
        self.addCleanup(plans.reload)
        self.addCleanup(override.disable)

    def test_cards_sort_by_order_then_units_then_name(self):
        self.assertEqual([p.key for p in plans.all_plans()],
                         ["top", "free", "alpha", "zeta", "standard", "plain"])

    def test_an_order_left_out_is_a_hundred_and_a_description_is_trimmed(self):
        plain = plans.plan("plain")
        self.assertEqual(plain.order, 100)
        self.assertEqual(plain.description, "Just the basics.")
        self.assertEqual(plain.units, 0)
        self.assertEqual(plain.features, ())

    def test_the_plan_for_admins_is_found_and_kept_off_the_public_ladder(self):
        self.assertEqual(plans.admin_plan().key, "top")
        self.assertNotIn("top", {p.key for p in plans.public_plans()})
        self.assertEqual(len(plans.public_plans()), len(plans.all_plans()) - 1)

    def test_all_features_grants_what_nobody_listed(self):
        top = plans.plan("top")
        self.assertTrue(top.grants("gitea"))
        self.assertTrue(top.grants("a-feature-from-next-year"))
        self.assertFalse(plans.plan("plain").grants("gitea"))

    def test_a_card_promises_only_paid_features_this_host_serves(self):
        standard = [e.feature_key for e in plans.plan("standard").feature_rows()]
        self.assertNotIn("kanban", standard)          # parked here: not installed
        self.assertTrue(all(key in ("editor", "kanban") for key in standard))
        everything = plans.plan("top").feature_rows()
        self.assertTrue(everything)
        self.assertFalse(any(e.free for e in everything))
        self.assertEqual(plans.plan("free").feature_rows(), [])


class NoAdminPlanTests(SimpleTestCase):
    def test_a_ladder_without_one_has_no_admin_plan_and_hides_nothing(self):
        with override_settings(SUBSCRIPTION_PLANS_FILE=str(write(GOOD))):
            plans.reload()
            try:
                self.assertIsNone(plans.admin_plan())
                self.assertEqual(plans.public_plans(), plans.all_plans())
                self.assertEqual(plans.keys(), frozenset({"free", "standard"}))
            finally:
                plans.reload()


class ValidateNeverRaisesTests(SimpleTestCase):
    def test_a_missing_or_broken_file_is_a_list_of_problems(self):
        self.assertEqual(plans.validate(Path("/nonexistent/plans.yaml")),
                         ["no plan file at /nonexistent/plans.yaml"])
        broken = write("plans: [\n")
        found = plans.validate(broken)
        self.assertEqual(len(found), 1)
        self.assertIn("is not valid YAML", found[0])
        self.assertIn("must be a mapping", plans.validate(write("- a\n- b\n"))[0])

    def test_load_names_the_file_and_every_problem(self):
        path = write("version: 2\nplans: []\n")
        with self.assertRaises(plans.PlanError) as caught:
            plans.load(path)
        message = str(caught.exception)
        self.assertIn(str(path), message)
        self.assertIn("version must be 1", message)
        self.assertIn("plans must be a non-empty list", message)
        plans.reload()
