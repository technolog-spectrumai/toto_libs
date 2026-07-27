from django.test import SimpleTestCase, TestCase

from toto.connectors.services import mapping

from .helpers import DEFAULT_MAPPING_SPEC, make_bento_templates


class GetPathTests(SimpleTestCase):
    def test_walks_nested_dicts(self):
        self.assertEqual(mapping.get_path({"a": {"b": 1}}, "a.b"), 1)

    def test_numeric_segments_index_lists(self):
        record = {"ids": [{"value": "x"}, {"value": "y"}]}
        self.assertEqual(mapping.get_path(record, "ids.1.value"), "y")

    def test_empty_path_returns_object(self):
        self.assertEqual(mapping.get_path([1, 2], ""), [1, 2])

    def test_any_miss_returns_none(self):
        self.assertIsNone(mapping.get_path({"a": 1}, "a.b"))
        self.assertIsNone(mapping.get_path({"a": [1]}, "a.5"))
        self.assertIsNone(mapping.get_path({"a": [1]}, "a.x"))
        self.assertIsNone(mapping.get_path(None, "a"))


class ResolveValueTests(SimpleTestCase):
    def test_path(self):
        self.assertEqual(mapping.resolve_value({"a": 2}, {"path": "a"}), 2)

    def test_const(self):
        self.assertEqual(mapping.resolve_value({}, {"const": "fixed"}), "fixed")

    def test_template(self):
        record = {"title": "Book", "year": 1999}
        value = mapping.resolve_value(record, {"template": "{title} ({year})"})
        self.assertEqual(value, "Book (1999)")

    def test_template_with_missing_placeholder_is_none(self):
        self.assertIsNone(mapping.resolve_value({"title": "Book"}, {"template": "{title} ({year})"}))

    def test_invalid_source_is_none(self):
        self.assertIsNone(mapping.resolve_value({}, {}))
        self.assertIsNone(mapping.resolve_value({}, "a"))


class CheckWhenTests(SimpleTestCase):
    def test_no_filter_always_true(self):
        self.assertTrue(mapping.check_when({}, None))
        self.assertTrue(mapping.check_when({}, {}))

    def test_truthy(self):
        self.assertTrue(mapping.check_when({"a": "x"}, {"path": "a", "op": "truthy"}))
        self.assertFalse(mapping.check_when({"a": ""}, {"path": "a", "op": "truthy"}))

    def test_eq_ne(self):
        self.assertTrue(mapping.check_when({"a": 1}, {"path": "a", "op": "eq", "value": 1}))
        self.assertTrue(mapping.check_when({"a": 1}, {"path": "a", "op": "ne", "value": 2}))

    def test_contains(self):
        self.assertTrue(mapping.check_when({"a": [1, 2]}, {"path": "a", "op": "contains", "value": 2}))
        self.assertFalse(mapping.check_when({"a": None}, {"path": "a", "op": "contains", "value": 2}))


class StructuralValidationTests(SimpleTestCase):
    def _errors(self, spec):
        return mapping.validate_mapping_spec(spec, check_bento=False)

    def test_valid_spec_passes(self):
        self.assertEqual(self._errors(DEFAULT_MAPPING_SPEC), [])

    def test_not_a_dict(self):
        self.assertTrue(self._errors([]))

    def test_requires_nodes(self):
        self.assertTrue(any("nodes" in e for e in self._errors({"version": 1})))

    def test_bad_version(self):
        self.assertTrue(any("version" in e for e in self._errors(
            {"version": 2, "nodes": DEFAULT_MAPPING_SPEC["nodes"]})))

    def test_duplicate_rule_ids(self):
        spec = {
            "version": 1,
            "nodes": [
                {"rule_id": "a", "category_slug": "x", "identifier": {"path": "p"}},
                {"rule_id": "a", "category_slug": "x", "identifier": {"path": "p"}},
            ],
        }
        self.assertTrue(any("duplicate" in e for e in self._errors(spec)))

    def test_bad_value_source(self):
        spec = {
            "version": 1,
            "nodes": [{
                "rule_id": "a", "category_slug": "x",
                "identifier": {"path": "p"},
                "properties": {"name": {"path": "p", "const": "x"}},
            }],
        }
        self.assertTrue(any("exactly one" in e for e in self._errors(spec)))

    def test_dangling_endpoint_rule(self):
        spec = {
            "version": 1,
            "nodes": [{"rule_id": "a", "category_slug": "x", "identifier": {"path": "p"}}],
            "relationships": [{
                "rule_id": "r", "edge_type_slug": "e",
                "from_rule": "a", "to_rule": "missing",
            }],
        }
        self.assertTrue(any("to_rule" in e for e in self._errors(spec)))

    def test_when_needs_value_for_eq(self):
        spec = {
            "version": 1,
            "nodes": [{
                "rule_id": "a", "category_slug": "x",
                "identifier": {"path": "p"},
                "when": {"path": "p", "op": "eq"},
            }],
        }
        self.assertTrue(any("requires a 'value'" in e for e in self._errors(spec)))


class BentoValidationTests(TestCase):
    def setUp(self):
        make_bento_templates()

    def test_valid_spec_passes(self):
        self.assertEqual(mapping.validate_mapping_spec(DEFAULT_MAPPING_SPEC), [])

    def test_unknown_category(self):
        spec = {
            "version": 1,
            "nodes": [{"rule_id": "a", "category_slug": "ghost", "identifier": {"path": "p"}}],
        }
        self.assertTrue(any("unknown category" in e for e in mapping.validate_mapping_spec(spec)))

    def test_undeclared_property(self):
        spec = {
            "version": 1,
            "nodes": [{
                "rule_id": "a", "category_slug": "person",
                "identifier": {"path": "p"},
                "properties": {"shoe_size": {"path": "p"}},
            }],
        }
        self.assertTrue(any("not declared" in e for e in mapping.validate_mapping_spec(spec)))

    def test_unknown_edge_type(self):
        spec = {
            "version": 1,
            "nodes": [{"rule_id": "a", "category_slug": "person", "identifier": {"path": "p"}}],
            "relationships": [{
                "rule_id": "r", "edge_type_slug": "ghost",
                "from_rule": "a", "to_rule": "a",
            }],
        }
        self.assertTrue(any("unknown edge type" in e for e in mapping.validate_mapping_spec(spec)))

    def test_disallowed_endpoint(self):
        spec = {
            "version": 1,
            "nodes": [
                {"rule_id": "p1", "category_slug": "person", "identifier": {"path": "p"}},
                {"rule_id": "p2", "category_slug": "person", "identifier": {"path": "q"}},
            ],
            "relationships": [{
                "rule_id": "r", "edge_type_slug": "works-at",
                "from_rule": "p1", "to_rule": "p2",   # target must be org
            }],
        }
        self.assertTrue(
            any("not an allowed target" in e for e in mapping.validate_mapping_spec(spec))
        )
