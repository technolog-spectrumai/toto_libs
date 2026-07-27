"""The Bento validation API the Ingestor relies on (SQL only — no Neo4j)."""

from django.test import TestCase

from toto.bento import graph_service as gs
from toto.bento.models import BentoCategory, BentoEdgeType


class ValidatorTests(TestCase):
    def setUp(self):
        self.person = BentoCategory.objects.create(
            name="Person", slug="person", neo4j_label="Person",
            property_schema=[
                {"name": "name", "type": "string", "required": True},
                {"name": "age", "type": "integer"},
            ],
            searchable_properties=["name"],
        )
        self.org = BentoCategory.objects.create(
            name="Organization", slug="organization", neo4j_label="Organization",
            property_schema=[{"name": "name", "type": "string", "required": True}],
        )
        self.works_at = BentoEdgeType.objects.create(
            name="Works at", slug="works-at", rel_type="WORKS_AT",
            property_schema=[{"name": "since", "type": "integer"}],
        )
        self.works_at.allowed_sources.set([self.person])
        self.works_at.allowed_targets.set([self.org])

    def test_validate_node_ok(self):
        self.assertEqual(gs.validate_node("person", {"name": "Ada", "age": 30}), [])

    def test_validate_node_missing_required(self):
        errors = gs.validate_node("person", {"age": 30})
        self.assertTrue(any("required" in e for e in errors))

    def test_validate_node_bad_type(self):
        errors = gs.validate_node("person", {"name": "Ada", "age": "old"})
        self.assertTrue(errors)

    def test_validate_node_unknown_category(self):
        self.assertTrue(gs.validate_node("ghost", {}))

    def test_validate_edge_ok(self):
        self.assertEqual(gs.validate_edge("works-at", "person", "organization", {"since": 2020}), [])

    def test_validate_edge_bad_endpoints(self):
        errors = gs.validate_edge("works-at", "organization", "person", {})
        self.assertTrue(any("allowed source" in e for e in errors))

    def test_edge_types_between_respects_constraints(self):
        forward = [et.slug for et in gs.edge_types_between("person", "organization")]
        self.assertIn("works-at", forward)
        reverse = [et.slug for et in gs.edge_types_between("organization", "person")]
        self.assertNotIn("works-at", reverse)

    def test_searchable_property_names_configured(self):
        self.assertEqual(gs.searchable_property_names(self.person), ["name"])

    def test_searchable_property_names_falls_back_to_name(self):
        self.assertEqual(gs.searchable_property_names(self.org), ["name"])
