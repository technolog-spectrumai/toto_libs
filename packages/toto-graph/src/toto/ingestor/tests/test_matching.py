"""Duplicate / fuzzy matching — robust across rapidfuzz and the difflib fallback."""

from django.test import SimpleTestCase

from toto.ingestor.services.catalog import CatalogEntry
from toto.ingestor.services import matching


def _entry(surface, uid, slug="person"):
    from toto.ingestor.services.text import normalize
    return CatalogEntry(surface, normalize(surface), uid, slug, surface, "name")


class MatchingTests(SimpleTestCase):
    def setUp(self):
        self.entries = [
            _entry("Ada Lovelace", "u1"),
            _entry("Charles Babbage", "u2"),
            _entry("Acme Corporation", "u3", "organization"),
        ]

    def test_exact_normalized_match_is_duplicate(self):
        cands, is_dup = matching.match_surface("  ada   lovelace ", self.entries)
        self.assertTrue(is_dup)
        self.assertEqual(cands[0]["uid"], "u1")
        self.assertEqual(cands[0]["method"], "exact")
        self.assertEqual(cands[0]["score"], 1.0)

    def test_close_typo_surfaces_a_candidate(self):
        cands, _ = matching.match_surface("Ada Lovelce", self.entries)
        self.assertTrue(cands)
        self.assertEqual(cands[0]["uid"], "u1")
        self.assertGreaterEqual(cands[0]["score"], 0.7)

    def test_unrelated_surface_has_no_candidates(self):
        cands, is_dup = matching.match_surface("Zzzzq Qwxptn", self.entries)
        self.assertFalse(cands)
        self.assertFalse(is_dup)

    def test_empty_surface(self):
        self.assertEqual(matching.match_surface("", self.entries), ([], False))

    def test_index_is_deterministic_ordering(self):
        a, _ = matching.match_surface("Acme Corporatio", self.entries)
        b, _ = matching.match_surface("Acme Corporatio", self.entries)
        self.assertEqual([c["uid"] for c in a], [c["uid"] for c in b])
