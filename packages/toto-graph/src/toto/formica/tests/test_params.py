from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from toto.formica import params as params_mod
from toto.formica.models import Colony


class ParamSpecTests(SimpleTestCase):
    def test_defaults_cover_every_spec(self):
        defaults = params_mod.defaults()
        self.assertEqual(set(defaults), set(params_mod.PARAM_SPECS))

    def test_every_default_is_in_range(self):
        for name, spec in params_mod.PARAM_SPECS.items():
            self.assertGreaterEqual(spec["default"], spec["min"], name)
            self.assertLessEqual(spec["default"], spec["max"], name)

    def test_every_spec_has_a_group_and_help(self):
        for name, spec in params_mod.PARAM_SPECS.items():
            self.assertIn(spec["group"], params_mod.PARAM_GROUPS, name)
            self.assertTrue(spec["help"], name)

    def test_validate_accepts_valid_params(self):
        self.assertEqual(
            params_mod.validate_params({"evaporation_rate": 0.2, "max_steps_per_ant": 5}),
            [],
        )

    def test_validate_rejects_unknown_keys(self):
        errors = params_mod.validate_params({"nonsense": 1})
        self.assertTrue(any("Unknown parameter" in e for e in errors))

    def test_validate_rejects_out_of_range(self):
        errors = params_mod.validate_params({"evaporation_rate": 0.9})
        self.assertTrue(any("between" in e for e in errors))

    def test_validate_rejects_wrong_types(self):
        self.assertTrue(params_mod.validate_params({"max_steps_per_ant": 2.5}))
        self.assertTrue(params_mod.validate_params({"alpha": "high"}))
        self.assertTrue(params_mod.validate_params({"alpha": True}))
        self.assertTrue(params_mod.validate_params([1, 2]))

    def test_resolve_layers_defaults_colony_caste(self):
        merged = params_mod.resolve({"alpha": 2.0}, {"alpha": 3.0, "beta": 0.5})
        self.assertEqual(merged["alpha"], 3.0)   # caste override wins
        self.assertEqual(merged["beta"], 0.5)
        self.assertEqual(merged["evaporation_rate"],
                         params_mod.PARAM_SPECS["evaporation_rate"]["default"])

    def test_resolve_drops_unknown_keys(self):
        merged = params_mod.resolve({"bogus": 1})
        self.assertNotIn("bogus", merged)


class ColonyParamValidationTests(TestCase):
    def test_clean_rejects_bad_meta_params(self):
        colony = Colony(name="C", meta_params={"evaporation_rate": 5.0})
        with self.assertRaises(ValidationError):
            colony.full_clean()

    def test_clean_rejects_non_list_protected_sets(self):
        colony = Colony(name="C", protected_labels="person")
        with self.assertRaises(ValidationError):
            colony.full_clean()

    def test_params_method_resolves(self):
        colony = Colony(name="C", meta_params={"prune_threshold": 0.2})
        self.assertEqual(colony.params()["prune_threshold"], 0.2)
        self.assertEqual(colony.params({"prune_threshold": 0.3})["prune_threshold"], 0.3)
