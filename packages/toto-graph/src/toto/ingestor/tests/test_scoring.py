from django.test import SimpleTestCase

from toto.ingestor.services import scoring


class NodeScoringTests(SimpleTestCase):
    def test_existing_exact_is_full_confidence(self):
        node = {"kind": "existing", "match": {"method": "exact", "score": 1.0}}
        self.assertEqual(scoring.node_confidence(node), 1.0)

    def test_existing_fuzzy_uses_match_score(self):
        node = {"kind": "existing", "match": {"method": "fuzzy", "score": 0.83}}
        self.assertEqual(scoring.node_confidence(node), 0.83)

    def test_new_categorized_beats_uncategorized(self):
        cat = scoring.node_confidence({"kind": "new", "category_slug": "person", "ner_label": "PERSON"})
        uncat = scoring.node_confidence({"kind": "new", "category_slug": None})
        self.assertGreater(cat, uncat)

    def test_clamped_to_unit_interval(self):
        v = scoring.node_confidence({"kind": "new", "category_slug": "person", "ner_label": "PERSON"})
        self.assertGreaterEqual(v, 0.0)
        self.assertLessEqual(v, 1.0)


class RelScoringTests(SimpleTestCase):
    def test_trigger_match_raises_confidence(self):
        with_trigger = scoring.relationship_confidence({"trigger_matched": True})
        without = scoring.relationship_confidence({"trigger_matched": False})
        self.assertGreater(with_trigger, without)
