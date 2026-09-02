"""The plan registry: what the file may say, and what it may not.

Every rule in PLANS.md's validation table has a test here, because the whole
argument for generating plans instead of storing them is that a mistake is
caught at build time. A validator nobody exercises is a table of promises.

These tests write a temporary YAML file and point
``settings.SUBSCRIPTION_PLANS_FILE`` at it. They never touch the database:
this module is about the file, and `tests.py` is about what the file means.
"""

from __future__ import annotations

import tempfile
from dataclasses import FrozenInstanceError
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from toto.subscriptions import plans


def write(text: str) -> Path:
    """A plan file on disk, for the life of the process."""
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", prefix="plans-", delete=False)
    handle.write(text)
    handle.close()
    return Path(handle.name)


#: The smallest file that validates. Every negative test below is this with
#: exactly one thing wrong, so a failure names the rule it broke.
GOOD = """
version: 1
plans:
  - key: free
    name: Free
    default: true
    units: 0
    order: 10
  - key: standard
    name: Standard
    units: 200
    order: 20
    features: [editor, kanban]
"""


class ValidationTestCase(SimpleTestCase):
    def problems(self, text: str) -> list[str]:
        with override_settings(SUBSCRIPTION_PLANS_FILE=str(write(text))):
            plans.reload()
            found = plans.validate()
        plans.reload()
        return found

    def assertRejected(self, text: str, fragment: str):
        found = self.problems(text)
        self.assertTrue(found, "the validator accepted a file it must refuse")
        self.assertTrue(
            any(fragment in problem for problem in found),
            f"no problem mentioned {fragment!r}; got {found}")


class GoodFileTests(ValidationTestCase):
    def test_the_minimal_file_validates(self):
        """The control. If this ever fails, every rejection below is noise."""
        self.assertEqual(self.problems(GOOD), [])

    def test_the_shipped_ladder_validates(self):
        """What this suite actually sells."""
        self.assertEqual(plans.validate(plans.DEFAULT_PLANS_FILE), [])

    def test_the_example_file_validates(self):
        """The file people copy.

        An example that has quietly rotted is worse than no example, and this
        is the only thing standing between it and that.
        """
        example = plans.DEFAULT_PLANS_FILE.parent / "plans.example.yaml"
        self.assertTrue(example.exists(), "plans.example.yaml is not shipped")
        self.assertEqual(plans.validate(example), [])


class KeyTests(ValidationTestCase):
    def test_a_duplicate_plan_key_is_refused(self):
        """Two tiers with one identity: a subscription could not say which."""
        self.assertRejected(GOOD + """
  - key: standard
    name: Standard Again
    units: 300
""", "duplicate plan_key")

    def test_a_key_that_is_not_a_slug_is_refused(self):
        """The key appears in a URL captured as <slug:plan_key>. A key the
        route cannot capture is a plan nobody can subscribe to."""
        for bad in ("Standard", "2fast", "with space", "trailing!"):
            with self.subTest(key=bad):
                self.assertRejected(
                    GOOD.replace("key: standard", f"key: {bad!r}"), "must match")

    def test_a_missing_key_is_refused(self):
        self.assertRejected(
            GOOD.replace("  - key: standard\n    name: Standard\n",
                         "  - name: Standard\n"),
            "must match")

    def test_a_missing_name_is_refused(self):
        self.assertRejected(GOOD.replace("    name: Standard\n", ""),
                            "name is required")

    def test_a_hyphen_is_a_legal_key(self):
        """`set_offers` parses `aud-<pk>-<plan_key>` with partition, not
        split, precisely so this stays true."""
        self.assertEqual(
            self.problems(GOOD.replace("key: standard", "key: small-team")
                              .replace("[editor, kanban]", "[editor]")),
            [])


class FeatureTests(ValidationTestCase):
    def test_an_unknown_feature_key_is_refused(self):
        """The failure this catches: a tier that sells a room nobody built."""
        self.assertRejected(GOOD.replace("[editor, kanban]", "[editor, telepathy]"),
                            "unknown feature_key")

    def test_a_free_feature_cannot_be_sold(self):
        """`vault` is free for everybody. A plan listing it would read as
        though the tier were what unlocks your own files."""
        self.assertRejected(GOOD.replace("[editor, kanban]", "[editor, vault]"),
                            "FREE feature")

    def test_the_same_feature_twice_is_refused(self):
        self.assertRejected(GOOD.replace("[editor, kanban]", "[editor, editor]"),
                            "the same key twice")

    def test_features_must_be_a_list(self):
        self.assertRejected(GOOD.replace("[editor, kanban]", "editor"),
                            "features must be a list")

    def test_a_feature_that_is_not_a_string_is_refused(self):
        self.assertRejected(GOOD.replace("[editor, kanban]", "[editor, 7]"),
                            "must be a string")

    def test_no_features_at_all_is_fine(self):
        """The free tier's normal shape."""
        self.assertEqual(
            self.problems(GOOD.replace("    features: [editor, kanban]\n", "")), [])


class DefaultTests(ValidationTestCase):
    def test_no_default_is_refused(self):
        """Something has to be what a person has before they buy anything."""
        self.assertRejected(GOOD.replace("    default: true\n", ""),
                            "exactly one plan must set default")

    def test_two_defaults_are_refused(self):
        self.assertRejected(GOOD.replace("    units: 200", "    default: true\n    units: 200"),
                            "exactly one plan must set default")

    def test_a_default_that_costs_units_is_refused(self):
        """Nobody agreed to pay for what they were given."""
        self.assertRejected(GOOD.replace("    default: true\n    units: 0",
                                         "    default: true\n    units: 50"),
                            "cannot cost units")

    def test_default_must_be_a_boolean(self):
        self.assertRejected(GOOD.replace("default: true", 'default: "yes"'),
                            "must be true or false")


class ShapeTests(ValidationTestCase):
    def test_an_unknown_top_level_key_is_refused(self):
        """Silently ignoring it is how a typo becomes a setting that does
        nothing for a year."""
        self.assertRejected(GOOD + "\ncurrency: PLN\n", "unknown top-level key")

    def test_an_unknown_plan_key_is_refused(self):
        self.assertRejected(GOOD.replace("    units: 200", "    price: 200"),
                            "unknown key(s)")

    def test_the_wrong_version_is_refused(self):
        self.assertRejected(GOOD.replace("version: 1", "version: 2"),
                            "version must be 1")

    def test_units_must_be_a_whole_number(self):
        self.assertRejected(GOOD.replace("units: 200", 'units: "200"'),
                            "units must be a whole number")

    def test_order_must_be_a_whole_number(self):
        self.assertRejected(GOOD.replace("order: 20", "order: 20.5"),
                            "order must be a whole number")

    def test_an_empty_plans_list_is_refused(self):
        self.assertRejected("version: 1\nplans: []\n", "non-empty list")

    def test_a_plan_that_is_not_a_mapping_is_refused(self):
        self.assertRejected("version: 1\nplans: [standard]\n", "must be a mapping")

    def test_malformed_yaml_is_refused_by_name(self):
        """A parse error must say WHICH file and that it is the YAML."""
        self.assertRejected("version: 1\nplans:\n  - key: [unclosed\n",
                            "not valid YAML")

    def test_a_document_that_is_not_a_mapping_is_refused(self):
        self.assertRejected("- free\n- standard\n", "must be a mapping")

    def test_a_missing_file_is_refused_by_path(self):
        with override_settings(SUBSCRIPTION_PLANS_FILE="/nonexistent/plans.yaml"):
            plans.reload()
            found = plans.validate()
        plans.reload()
        self.assertEqual(len(found), 1)
        self.assertIn("no plan file at", found[0])

    def test_every_problem_is_reported_not_just_the_first(self):
        """An operator fixing one fault per deploy is a bad afternoon."""
        found = self.problems("""
version: 9
plans:
  - key: Free
    units: "0"
""")
        self.assertGreaterEqual(len(found), 4)


class RegistryTests(SimpleTestCase):
    """What the file becomes."""

    def setUp(self):
        self.override = override_settings(SUBSCRIPTION_PLANS_FILE=str(write(GOOD)))
        self.override.enable()
        plans.reload()
        self.addCleanup(plans.reload)
        self.addCleanup(self.override.disable)

    def test_a_plan_is_frozen(self):
        """'Immutable' has to mean the attribute assignment fails."""
        with self.assertRaises(FrozenInstanceError):
            plans.plan("standard").units = 1

    def test_features_is_a_tuple_not_a_list(self):
        """A list inside a frozen dataclass is exactly the hole `frozen`
        is meant to close: `plan.features.append(...)` would work."""
        self.assertIsInstance(plans.plan("standard").features, tuple)

    def test_a_bad_file_raises_rather_than_installing_half_a_registry(self):
        with override_settings(SUBSCRIPTION_PLANS_FILE=str(write("version: 2\nplans: []\n"))):
            plans.reload()
            with self.assertRaises(plans.PlanError):
                plans.load()

    def test_the_override_replaces_the_shipped_ladder_it_does_not_merge(self):
        """professional is in plans.yaml and not in GOOD. If it survives the
        override, a host cannot actually say what it sells."""
        self.assertEqual(sorted(plans.keys()), ["free", "standard"])

    def test_dropping_the_override_returns_the_shipped_ladder(self):
        """`_ensure` re-resolves when the source path changes — the reason
        the cache is a module global and not an lru_cache."""
        self.override.disable()
        self.addCleanup(self.override.enable)
        plans.reload()
        self.assertIn("professional", plans.keys())

    def test_cards_come_back_in_order(self):
        self.assertEqual([p.key for p in plans.all_plans()], ["free", "standard"])

    def test_the_default_plan_is_found_by_flag_not_by_position(self):
        self.assertEqual(plans.default_plan().key, "free")

    def test_a_missing_plan_raises_naming_what_is_valid(self):
        """An operator reading this traceback needs the answer in it."""
        with self.assertRaises(plans.PlanError) as caught:
            plans.plan("enterprise")
        self.assertIn("standard", str(caught.exception))

    def test_get_answers_none_for_a_key_that_left_the_file(self):
        """The stale-key case. A retired tier must not crash the people who
        were on it — `plan_for` falls back to the default."""
        self.assertIsNone(plans.get("enterprise"))

    def test_grants_reads_the_declared_features(self):
        self.assertTrue(plans.plan("standard").grants("editor"))
        self.assertFalse(plans.plan("standard").grants("cyprian"))
        self.assertFalse(plans.plan("free").grants("editor"))
