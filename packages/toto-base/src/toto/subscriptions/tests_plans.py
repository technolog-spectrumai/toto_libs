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

from django.test import SimpleTestCase, TestCase, override_settings

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


class TheFreeTierIsWhatItClaimsTests(SimpleTestCase):
    """What a member with no subscription actually gets, asserted by name.

    The free tier is a PROMISE — "you keep your identity and your files, and
    you can always reach the money" — and a promise that lives only in a
    docstring drifts. On 2026-09-06 chat and the exchange left it, which is
    exactly the kind of change that should be visible in a diff rather than
    discovered by a member.
    """

    def test_the_free_tier_is_identity_files_and_the_money(self):
        from django.apps import apps

        from toto.subscriptions.catalogue import registry

        free = {e.feature_key for e in registry.all() if e.free}
        expected = {"core", "socialhub", "people", "vault", "events",
                    "assets", "quota", "subscriptions", "workflows", "jess"}
        # Deliberate (2026-10-06): geography is free on every plan. The map,
        # a person's point and a community's headquarters open to every
        # member, and what costs is priced per action in mana, not by plan.
        # The app declares it itself (toto/geography/entitlements.py, found
        # by autodiscovery), so it is in the catalogue exactly on a host that
        # installs toto-geo's app.
        if apps.is_installed("toto.geography"):
            expected.add("geography")
        # Deliberate too (2026-10-06, stage 65): a company is a community,
        # and its ID number and share register are free on every plan
        # (toto/companies/entitlements.py), where the app is installed.
        if apps.is_installed("toto.companies"):
            expected.add("companies")
        self.assertEqual(
            free, expected,
            "the free tier changed — if that is deliberate, say so here")

    def test_the_exchange_is_not_free(self):
        """Moved out on 2026-09-06, with chat. Asserted separately from the
        set above so the failure names the feature rather than a diff of ten
        keys."""
        from toto.subscriptions.catalogue import registry

        entitlement = registry.get("bourse")
        self.assertIsNotNone(entitlement)
        self.assertFalse(entitlement.free)

    def test_the_forum_is_not_declared_and_no_plan_names_it(self):
        """Parked on 2026-10-09: its entry left the catalogue and its line
        the plan file in one edit, because a plan naming an undeclared key
        stops the build (E001). The library's own ladder is read too, not
        only the one this host points at."""
        from django.test import override_settings

        from toto.subscriptions import plans
        from toto.subscriptions.catalogue import registry

        self.assertIsNone(registry.get("forum"))
        self.assertNotIn("forum", {e.feature_key for e in registry.all()})

        def named():
            return {plan.key for plan in plans.all_plans() if "forum" in plan.features}

        self.assertEqual(named(), set())
        self.addCleanup(plans.reload)
        with override_settings(SUBSCRIPTION_PLANS_FILE=str(plans.DEFAULT_PLANS_FILE)):
            plans.reload()
            self.assertTrue(plans.all_plans(), "no plan at all: the check is vacuous")
            self.assertEqual(named(), set())

    def test_the_operator_tools_are_declared_and_sold(self):
        """`monit` was undeclared before 2026-09-06, which made it free BY
        OMISSION — `is_entitled` answers True for an app the catalogue does
        not know. Declaring it is what makes the tier a decision rather than
        an accident.

        `sepulka` was declared beside it that day and left on 2026-09-23 with
        the sealed-backup app, catalogue entry and plan grant together.
        `yamabiko` (bucket echoes) joined the same day — one word here."""
        from toto.subscriptions import plans
        from toto.subscriptions.catalogue import registry

        professional = next(p for p in plans.all_plans()
                            if p.key == "professional")
        for key in ("monit", "yamabiko"):
            with self.subTest(feature=key):
                self.assertIsNotNone(registry.get(key))
                self.assertFalse(registry.get(key).free)
                self.assertTrue(professional.grants(key))

    def test_every_paid_feature_is_granted_by_some_plan(self):
        """The W001 rule, as a test rather than only a system check: a feature
        declared paid and granted by nobody hides its tile from everybody and
        402s its writes. It has happened three times — the compute tier,
        ocr/fileservices, and ocr again on 2026-09-06."""
        from toto.subscriptions import plans
        from toto.subscriptions.catalogue import registry

        granted = {key for plan in plans.all_plans() for key in plan.features}
        orphans = sorted(e.feature_key for e in registry.all()
                         if not e.free and e.feature_key not in granted)
        self.assertEqual(orphans, [],
                         f"{orphans} are sold by no plan and so reach nobody")


class AdminOnlyAndAllFeaturesTests(TestCase):
    """1.51: a Superuser tier — admin_only + all_features."""

    LADDER = """version: 1
plans:
  - key: free
    name: Free
    default: true
  - key: standard
    name: Standard
    features: [editor]
  - key: superuser
    name: Superuser
    admin_only: true
    all_features: true
"""

    def setUp(self):
        import tempfile
        from pathlib import Path

        from django.test import override_settings

        from . import plans

        folder = Path(tempfile.mkdtemp())
        (folder / "plans.yaml").write_text(self.LADDER)
        self.override = override_settings(SUBSCRIPTION_PLANS_FILE=str(folder / "plans.yaml"))
        self.override.enable()
        plans.reload()
        self.addCleanup(plans.reload)
        self.addCleanup(self.override.disable)

    def test_the_admin_plan_grants_everything_and_hides_from_everyone_else(self):
        from django.contrib.auth import get_user_model

        from . import plans, services
        from .models import plan_for

        User = get_user_model()
        admin = User.objects.create_superuser("root", password="x")
        staff = User.objects.create_user("st", password="x", is_staff=True)
        member = User.objects.create_user("m", password="x")
        top = plans.plan("superuser")
        self.assertTrue(top.grants("anything-at-all"))
        self.assertTrue(top.admin_only and top.for_admins)   # the older spelling, read
        self.assertEqual([p.key for p in plans.public_plans()], ["free", "standard"])
        # The privilege alone puts nobody on the plan: the superuser must hold
        # it. Eligible without any Community offer since 2026-09-28.
        self.assertEqual(plan_for(admin).key, "free")
        self.assertTrue(services.is_eligible(admin, "superuser"))
        from django.core.management import call_command
        from io import StringIO

        call_command("bootstrap_plans", stdout=StringIO())
        admin = User.objects.get(pk=admin.pk)        # a Person was made for it
        self.assertTrue(services.is_eligible(admin, "superuser"))
        self.assertEqual(plan_for(admin).key, "superuser")
        self.assertEqual(plan_for(member).key, "free")
        self.assertFalse(services.is_eligible(staff, "superuser"))
        self.assertFalse(services.is_eligible(member, "superuser"))
        self.assertNotIn("superuser", [p.key for p in services.eligible_plans(staff)])

    def test_the_default_plan_cannot_be_admin_only(self):
        import tempfile
        from pathlib import Path

        from . import plans

        path = Path(tempfile.mkdtemp()) / "bad.yaml"
        path.write_text("version: 1\nplans:\n  - key: free\n    name: Free\n    default: true\n    admin_only: true\n")
        self.assertTrue(any("admin_only" in p for p in plans.validate(path)))


class ForAdminsSpellingTests(ValidationTestCase):
    """`for_admins` is the documented spelling of the admin flag; `admin_only`
    is the older one and still reads the same (2026-09-28)."""

    ADMIN = GOOD + """  - key: superuser
    name: Superuser
    all_features: true
"""

    def ladder(self, flags: str) -> str:
        return self.ADMIN + "".join(f"    {line}\n" for line in flags.splitlines())

    def built(self, text: str):
        with override_settings(SUBSCRIPTION_PLANS_FILE=str(write(text))):
            plans.reload()
            try:
                return plans.plan("superuser")
            finally:
                plans.reload()

    def test_for_admins_is_the_spelling(self):
        text = self.ladder("for_admins: true")
        self.assertEqual(self.problems(text), [])
        top = self.built(text)
        self.assertTrue(top.for_admins)
        self.assertTrue(top.admin_only)

    def test_admin_only_is_an_alias_for_it(self):
        text = self.ladder("admin_only: true")
        self.assertEqual(self.problems(text), [])
        self.assertTrue(self.built(text).for_admins)

    def test_both_may_be_stated_when_they_agree(self):
        text = self.ladder("for_admins: true\nadmin_only: true")
        self.assertEqual(self.problems(text), [])
        self.assertTrue(self.built(text).for_admins)
        self.assertFalse(self.built(self.ladder("for_admins: false\nadmin_only: false")).for_admins)

    def test_both_disagreeing_is_refused(self):
        for flags in ("for_admins: true\nadmin_only: false",
                      "for_admins: false\nadmin_only: true"):
            with self.subTest(flags=flags):
                self.assertRejected(self.ladder(flags), "for_admins and admin_only are one flag")

    def test_for_admins_must_be_a_boolean(self):
        self.assertRejected(self.ladder('for_admins: "yes"'), "for_admins must be true or false")

    def test_the_default_plan_cannot_be_for_admins(self):
        self.assertRejected(GOOD.replace("    default: true\n", "    default: true\n    for_admins: true\n"),
                            "the default plan cannot be for_admins")

    def test_left_out_it_is_an_ordinary_plan(self):
        text = self.ADMIN
        self.assertEqual(self.problems(text), [])
        self.assertFalse(self.built(text).for_admins)
