"""End-to-end with REAL spaCy + rapidfuzz, Neo4j stubbed at the catalog seam.

Exercises detection → matching → relations → validation → proposal (and the
pipeline orchestration) against live NLP. The only seam mocked is
``catalog.build_catalog`` (which would otherwise read Neo4j).
"""

import unittest
from unittest.mock import patch

from django.test import TestCase

from toto.bento.models import BentoCategory, BentoEdgeType
from toto.ingestor.services import detection, pipeline
from toto.ingestor.services.catalog import CatalogEntry
from toto.ingestor.services.proposal import assemble_from_catalog
from toto.ingestor.services.text import normalize


def _entry(surface, uid, slug):
    return CatalogEntry(surface, normalize(surface), uid, slug, surface, "name")


_NLP, _HAS_NER = detection._load_pipeline()


@unittest.skipUnless(_HAS_NER, "en_core_web_sm not installed")
class PipelineIntegrationTests(TestCase):
    def setUp(self):
        self.person = BentoCategory.objects.create(
            name="Person", slug="person", neo4j_label="Person",
            property_schema=[{"name": "name", "type": "string", "required": True}],
            searchable_properties=["name"],
        )
        self.org = BentoCategory.objects.create(
            name="Org", slug="organization", neo4j_label="Organization",
            property_schema=[{"name": "name", "type": "string", "required": True}],
            searchable_properties=["name"],
        )
        self.works_at = BentoEdgeType.objects.create(
            name="Works at", slug="works-at", rel_type="WORKS_AT", trigger_lemmas=["work"],
        )
        self.works_at.allowed_sources.set([self.person])
        self.works_at.allowed_targets.set([self.org])
        self.catalog = [_entry("Acme", "u-acme", "organization")]

    def test_detect_then_assemble_real_nlp(self):
        det = detection.detect("Ada Lovelace works at Acme.", self.catalog, {"person", "organization"})
        proposal = assemble_from_catalog(det, self.catalog)

        # Existing org resolved by the ruler.
        acme = [n for n in proposal["nodes"] if n.get("uid") == "u-acme"]
        self.assertTrue(acme)
        self.assertEqual(acme[0]["kind"], "existing")

        # New person suggested by NER, seeded + valid.
        people = [n for n in proposal["nodes"] if n["kind"] == "new" and n["category_slug"] == "person"]
        self.assertTrue(people, "expected a proposed Person node from NER")
        ada = people[0]
        self.assertIn("Ada", ada["display"])
        self.assertEqual(ada["validation"]["status"], "ok")

        # Relationship person → org via the 'work' trigger.
        rels = [r for r in proposal["relationships"] if r["edge_type_slug"] == "works-at"]
        self.assertTrue(rels, "expected a works-at relationship")
        rel = rels[0]
        self.assertEqual(rel["from"], ada["temp_id"])
        self.assertEqual(rel["to"], acme[0]["temp_id"])
        self.assertEqual(rel["validation"]["status"], "ok")

    def test_build_proposal_dict_orchestration(self):
        meta = {"node_count": 1, "entry_count": 1, "truncated": False}
        with patch.object(pipeline.catalog_svc, "build_catalog", return_value=(self.catalog, meta)):
            proposal, summary = pipeline.build_proposal_dict("Ada Lovelace works at Acme.")
        self.assertGreaterEqual(summary["new_nodes"], 1)
        self.assertEqual(summary["existing_nodes"], 1)
        self.assertTrue(summary["ner_available"])
        self.assertEqual(summary["catalog"], meta)

    def test_include_existing_nodes_links_across_sentences(self):
        # Ada (new, sentence 0) and Acme (existing, sentence 1) are in different
        # sentences. The works-at edge only links them when include_existing_nodes
        # broadens pairing beyond same-sentence co-occurrence.
        text = "Ada Lovelace is a mathematician. Many people work at Acme."
        meta = {"node_count": 1, "entry_count": 1, "truncated": False}
        with patch.object(pipeline.catalog_svc, "build_catalog", return_value=(self.catalog, meta)):
            without, _ = pipeline.build_proposal_dict(text)
            with_existing, _ = pipeline.build_proposal_dict(text, include_existing_nodes=True)

        def works_at(p):
            return [r for r in p["relationships"] if r["edge_type_slug"] == "works-at"]

        self.assertEqual(works_at(without), [], "no cross-sentence link by default")
        self.assertTrue(works_at(with_existing), "existing node linked across sentences when enabled")

    def test_fuzzy_duplicate_uses_rapidfuzz_path(self):
        # A near-duplicate of the catalog org should be flagged against u-acme.
        catalog = [_entry("Acme Corporation", "u-acme", "organization")]
        det = detection.detect("I joined Acme Corporaton last year.", catalog, {"organization"})
        proposal = assemble_from_catalog(det, catalog)
        flagged = [n for n in proposal["nodes"] if n.get("match") and n["match"].get("candidates")]
        self.assertTrue(
            any(c["uid"] == "u-acme" for n in flagged for c in n["match"]["candidates"]),
            "expected fuzzy match against the existing Acme Corporation node",
        )
