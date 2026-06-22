"""Deterministic relationship proposal (Bento endpoint constraints + triggers)."""

from django.test import TestCase

from toto.bento.models import BentoCategory, BentoEdgeType
from toto.ingestor.services import relations


class RelationsTests(TestCase):
    def setUp(self):
        self.person = BentoCategory.objects.create(name="Person", slug="person", neo4j_label="Person")
        self.org = BentoCategory.objects.create(name="Org", slug="organization", neo4j_label="Organization")
        self.works_at = BentoEdgeType.objects.create(
            name="Works at", slug="works-at", rel_type="WORKS_AT", trigger_lemmas=["work", "employ"],
        )
        self.works_at.allowed_sources.set([self.person])
        self.works_at.allowed_targets.set([self.org])

        self.ada = {"node_key": ("new", "person", "ada"), "category_slug": "person",
                    "sent_index": 0, "start_char": 0}
        self.acme = {"node_key": ("existing", "u3"), "category_slug": "organization",
                     "sent_index": 0, "start_char": 20}

    def test_trigger_lemma_proposes_directed_edge(self):
        forms = [{"ada", "work", "acme"}]
        rels = relations.propose([self.ada, self.acme], forms, ["Ada works at Acme."])
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["edge_type_slug"], "works-at")
        self.assertEqual(rels[0]["from_key"], self.ada["node_key"])
        self.assertEqual(rels[0]["to_key"], self.acme["node_key"])
        self.assertTrue(rels[0]["trigger_matched"])

    def test_no_trigger_for_edge_with_triggers_yields_nothing(self):
        # works-at declares triggers; absent from the sentence → not proposed.
        forms = [{"ada", "acme", "and"}]
        rels = relations.propose([self.ada, self.acme], forms, ["Ada and Acme."])
        self.assertEqual(rels, [])

    def test_cooccurrence_edge_without_triggers_is_proposed(self):
        knows = BentoEdgeType.objects.create(name="Knows", slug="knows", rel_type="KNOWS")
        knows.allowed_sources.set([self.person])
        knows.allowed_targets.set([self.org])
        forms = [{"ada", "acme"}]
        rels = relations.propose([self.ada, self.acme], forms, ["Ada Acme."])
        slugs = {r["edge_type_slug"] for r in rels}
        self.assertIn("knows", slugs)

    def test_endpoint_constraints_filter_reverse_direction(self):
        # Only person→org is allowed; org→person should never appear.
        forms = [{"ada", "work", "acme"}]
        rels = relations.propose([self.acme, self.ada], forms, ["Acme employs Ada."])
        for r in rels:
            self.assertEqual(r["from_key"], self.ada["node_key"])
            self.assertEqual(r["to_key"], self.acme["node_key"])
