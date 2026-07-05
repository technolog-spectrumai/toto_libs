from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from toto.connectors.services import transform

from .helpers import (
    BENTO_CATEGORIES,
    BENTO_EDGE_TYPES,
    BENTO_SEARCHABLE,
    DEFAULT_MAPPING_SPEC,
    entry,
    make_bento_templates,
)


def _transform(records, spec=None, entries=None):
    return transform.transform(
        records,
        spec or DEFAULT_MAPPING_SPEC,
        entries=entries or [],
        categories=BENTO_CATEGORIES,
        edge_types=BENTO_EDGE_TYPES,
        searchable=BENTO_SEARCHABLE,
    )


RECORD = {"author": {"name": "Ada Lovelace"}, "org": {"name": "Analytical Engines"}}


class TransformNodeTests(SimpleTestCase):
    def test_new_nodes_and_relationship(self):
        proposal, stats = _transform([RECORD])
        self.assertEqual(len(proposal["nodes"]), 2)
        self.assertEqual(len(proposal["relationships"]), 1)
        person = proposal["nodes"][0]
        self.assertEqual(person["kind"], "new")
        self.assertEqual(person["category_slug"], "person")
        self.assertEqual(person["display"], "Ada Lovelace")
        self.assertEqual(person["properties"]["name"], "Ada Lovelace")
        self.assertEqual(person["confidence"], transform.CONFIDENCE_NEW)
        self.assertIsNone(person["match"])
        rel = proposal["relationships"][0]
        self.assertEqual(rel["rel_type"], "WORKS_AT")
        self.assertEqual(rel["from"], "n1")
        self.assertEqual(rel["to"], "n2")
        self.assertEqual(stats["nodes_proposed"], 2)
        self.assertEqual(stats["relationships_proposed"], 1)

    def test_exact_match_becomes_existing_reference(self):
        entries = [entry("Ada Lovelace", "uid-ada", "person")]
        proposal, stats = _transform([RECORD], entries=entries)
        person = proposal["nodes"][0]
        self.assertEqual(person["kind"], "existing")
        self.assertEqual(person["uid"], "uid-ada")
        self.assertEqual(person["properties"], {})
        self.assertEqual(person["confidence"], transform.CONFIDENCE_EXISTING)
        self.assertEqual(person["match"]["method"], "exact")
        self.assertEqual(stats["existing_matched"], 1)

    def test_category_restricted_matching(self):
        # Same surface exists — but as an org, so the person rule must not match it.
        entries = [entry("Ada Lovelace", "uid-org", "org")]
        proposal, _stats = _transform([RECORD], entries=entries)
        person = proposal["nodes"][0]
        self.assertEqual(person["kind"], "new")

    def test_near_match_flags_duplicate(self):
        entries = [entry("Ada Lovelaces", "uid-ada", "person")]
        proposal, _stats = _transform([RECORD], entries=entries)
        person = proposal["nodes"][0]
        self.assertEqual(person["kind"], "new")
        self.assertTrue(person["duplicate_warning"])
        self.assertEqual(person["confidence"], transform.CONFIDENCE_NEW_DUPLICATE)
        self.assertEqual(person["match"]["method"], "fuzzy")
        self.assertTrue(person["match"]["candidates"])

    def test_identifier_property_filled_when_unmapped(self):
        spec = {
            "version": 1,
            "nodes": [{
                "rule_id": "person", "category_slug": "person",
                "identifier": {"path": "author.name"},
                # no explicit properties mapped
            }],
        }
        proposal, _stats = _transform([RECORD], spec=spec)
        self.assertEqual(proposal["nodes"][0]["properties"]["name"], "Ada Lovelace")

    def test_within_run_dedupe_accumulates_evidence(self):
        proposal, _stats = _transform([RECORD, dict(RECORD)])
        people = [n for n in proposal["nodes"] if n["category_slug"] == "person"]
        self.assertEqual(len(people), 1)
        self.assertEqual(len(people[0]["evidence"]), 2)
        # relationship deduped too
        self.assertEqual(len(proposal["relationships"]), 1)

    def test_when_filter_skips_rule(self):
        spec = {
            "version": 1,
            "nodes": [{
                "rule_id": "person", "category_slug": "person",
                "identifier": {"path": "author.name"},
                "when": {"path": "author.vip", "op": "truthy"},
            }],
        }
        proposal, _stats = _transform([RECORD], spec=spec)
        self.assertEqual(proposal["nodes"], [])

    def test_empty_identifier_skips_and_logs(self):
        proposal, stats = _transform([{"author": {}, "org": {"name": "Acme"}}])
        self.assertEqual(len(proposal["nodes"]), 1)  # only the org
        reasons = [s["reason"] for s in stats["skipped"]]
        self.assertIn("empty identifier", reasons)

    def test_relationship_skipped_when_endpoint_missing(self):
        proposal, stats = _transform([{"author": {"name": "Ada"}}])  # no org
        self.assertEqual(proposal["relationships"], [])
        reasons = [s["reason"] for s in stats["skipped"]]
        self.assertIn("endpoint rule did not fire", reasons)


class ExistingEdgeDedupTests(SimpleTestCase):
    def test_existing_to_existing_edge_already_in_graph_is_skipped(self):
        entries = [
            entry("Ada Lovelace", "uid-ada", "person"),
            entry("Analytical Engines", "uid-org", "org"),
        ]
        with patch.object(transform, "_edge_exists", return_value=True) as exists:
            proposal, stats = _transform([RECORD], entries=entries)
        exists.assert_called_once_with("works-at", "uid-ada", "uid-org")
        self.assertEqual(proposal["relationships"], [])
        self.assertEqual(stats["edges_already_in_graph"], 1)

    def test_existing_to_existing_edge_not_in_graph_is_proposed(self):
        entries = [
            entry("Ada Lovelace", "uid-ada", "person"),
            entry("Analytical Engines", "uid-org", "org"),
        ]
        with patch.object(transform, "_edge_exists", return_value=False):
            proposal, _stats = _transform([RECORD], entries=entries)
        self.assertEqual(len(proposal["relationships"]), 1)

    def test_new_endpoints_never_query_the_graph(self):
        with patch.object(transform, "_edge_exists") as exists:
            _transform([RECORD])
        exists.assert_not_called()


@override_settings(RAVIOLI_ENABLED=True)
class ProposalConformanceTests(TestCase):
    """The transform output must pass the ingestor's own validation layer."""

    def setUp(self):
        make_bento_templates()

    def test_output_revalidates_cleanly(self):
        from toto.ingestor.services import validation

        proposal, _stats = _transform([RECORD])
        validation.revalidate(proposal)
        for element in proposal["nodes"] + proposal["relationships"]:
            self.assertEqual(element["validation"]["status"], "ok", element)
        summary = validation.summarize(proposal)
        self.assertEqual(summary["new_nodes"], 2)
        self.assertEqual(summary["relationships"], 1)
        self.assertEqual(summary["errors"], 0)
