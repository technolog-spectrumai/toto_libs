"""Real spaCy detection (EntityRuler + NER). Skips gracefully without the model."""

import unittest

from django.test import SimpleTestCase

from toto.ingestor.services import detection
from toto.ingestor.services.catalog import CatalogEntry
from toto.ingestor.services.text import normalize


def _entry(surface, uid, slug):
    return CatalogEntry(surface, normalize(surface), uid, slug, surface, "name")


_NLP, _HAS_NER = detection._load_pipeline()
SPACY_OK = _NLP is not None


@unittest.skipUnless(SPACY_OK, "spaCy not installed")
class EntityRulerTests(SimpleTestCase):
    def test_existing_entities_detected_with_uid_and_category(self):
        entries = [_entry("Ada Lovelace", "u-ada", "person"), _entry("Acme", "u-acme", "organization")]
        det = detection.detect("Ada Lovelace works at Acme.", entries, {"person", "organization"})
        existing = {(m.uid, m.category_slug) for m in det.mentions if m.kind == "existing"}
        self.assertIn(("u-ada", "person"), existing)
        self.assertIn(("u-acme", "organization"), existing)

    def test_ruler_matching_is_case_insensitive(self):
        entries = [_entry("Acme", "u-acme", "organization")]
        det = detection.detect("We met ACME yesterday.", entries, {"organization"})
        self.assertTrue(any(m.uid == "u-acme" for m in det.mentions if m.kind == "existing"))

    def test_sentence_segmentation_and_lemma_forms(self):
        det = detection.detect("Ada worked hard. Bob rested.", [], set())
        self.assertGreaterEqual(len(det.sentences), 2)
        self.assertTrue(any("work" in forms for forms in det.sentence_forms))

    def test_detection_is_deterministic(self):
        entries = [_entry("Acme", "u-acme", "organization")]
        a = detection.detect("Acme grows. Acme wins.", entries, {"organization"})
        b = detection.detect("Acme grows. Acme wins.", entries, {"organization"})
        key = lambda d: [(m.text, m.kind, m.uid, m.category_slug) for m in d.mentions]
        self.assertEqual(key(a), key(b))


@unittest.skipUnless(_HAS_NER, "en_core_web_sm not installed")
class NerSuggestionTests(SimpleTestCase):
    def test_new_person_is_suggested_and_mapped_to_category(self):
        det = detection.detect("Angela Merkel visited Berlin.", [], {"person", "place", "organization"})
        new = [m for m in det.mentions if m.kind == "new"]
        self.assertTrue(new)
        self.assertIn("person", {m.category_slug for m in new})

    def test_unknown_category_left_uncategorized(self):
        # 'person' is not among the known slugs → PERSON maps to None for the user.
        det = detection.detect("Angela Merkel spoke today.", [], {"organization"})
        persons = [m for m in det.mentions if m.kind == "new" and m.ner_label == "PERSON"]
        self.assertTrue(persons)
        self.assertTrue(all(m.category_slug is None for m in persons))
