"""Proposal assembly: detection + matching + relations + validation → patch JSON.

Drives ``assemble_from_catalog`` with hand-built detection results so it needs
neither a live Neo4j nor the spaCy model (only SQL Bento templates).
"""

from django.test import TestCase

from toto.bento.models import BentoCategory, BentoEdgeType
from toto.ingestor.services.catalog import CatalogEntry
from toto.ingestor.services.detection import Detection, Mention
from toto.ingestor.services.proposal import assemble_from_catalog
from toto.ingestor.services.text import normalize
from toto.ingestor.services.validation import summarize


def _entry(surface, uid, slug):
    return CatalogEntry(surface, normalize(surface), uid, slug, surface, "name")


class AssemblyTests(TestCase):
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

    def _detect(self, mentions, sentence, forms):
        return Detection(
            mentions=mentions, sentences=[sentence], sentence_forms=[forms],
            ner_available=True, spacy_available=True,
        )

    def test_new_node_existing_node_and_relationship(self):
        mentions = [
            Mention("Ada", 0, 3, 0, "new", category_slug="person"),
            Mention("Acme", 13, 17, 0, "existing", category_slug="organization", uid="u-acme"),
        ]
        detection = self._detect(mentions, "Ada works at Acme.", {"ada", "work", "at", "acme"})

        proposal = assemble_from_catalog(detection, self.catalog)

        self.assertEqual([n["temp_id"] for n in proposal["nodes"]], ["n1", "n2"])
        ada, acme = proposal["nodes"]
        self.assertEqual(ada["kind"], "new")
        self.assertEqual(ada["category_slug"], "person")
        self.assertEqual(ada["properties"], {"name": "Ada"})
        self.assertEqual(ada["validation"]["status"], "ok")
        self.assertEqual(acme["kind"], "existing")
        self.assertEqual(acme["uid"], "u-acme")

        self.assertEqual(len(proposal["relationships"]), 1)
        rel = proposal["relationships"][0]
        self.assertEqual(rel["edge_type_slug"], "works-at")
        self.assertEqual((rel["from"], rel["to"]), ("n1", "n2"))
        self.assertEqual(rel["validation"]["status"], "ok")
        self.assertTrue(rel["trigger_matched"])

        summary = summarize(proposal)
        self.assertEqual(summary["new_nodes"], 1)
        self.assertEqual(summary["existing_nodes"], 1)
        self.assertEqual(summary["relationships"], 1)
        self.assertEqual(summary["errors"], 0)

    def test_uncategorized_new_node_is_a_validation_error(self):
        mentions = [Mention("Mystery", 0, 7, 0, "new", category_slug=None)]
        detection = self._detect(mentions, "Mystery.", {"mystery"})
        proposal = assemble_from_catalog(detection, self.catalog)
        self.assertEqual(proposal["nodes"][0]["validation"]["status"], "error")

    def test_duplicate_new_node_flags_warning_and_candidate(self):
        catalog = [_entry("Ada Lovelace", "u-ada", "person")]
        mentions = [Mention("Ada Lovelace", 0, 12, 0, "new", category_slug="person")]
        detection = self._detect(mentions, "Ada Lovelace.", {"ada", "lovelace"})
        proposal = assemble_from_catalog(detection, catalog)
        node = proposal["nodes"][0]
        self.assertTrue(node["duplicate_warning"])
        self.assertTrue(node["match"]["candidates"])
        self.assertEqual(node["match"]["candidates"][0]["uid"], "u-ada")

    def test_repeated_mentions_merge_into_one_node(self):
        mentions = [
            Mention("Ada", 0, 3, 0, "new", category_slug="person"),
            Mention("Ada", 20, 23, 1, "new", category_slug="person"),
        ]
        detection = Detection(
            mentions=mentions, sentences=["Ada is here.", "Ada again."],
            sentence_forms=[{"ada"}, {"ada"}], ner_available=True, spacy_available=True,
        )
        proposal = assemble_from_catalog(detection, self.catalog)
        self.assertEqual(len(proposal["nodes"]), 1)
        self.assertEqual(len(proposal["nodes"][0]["evidence"]), 2)
