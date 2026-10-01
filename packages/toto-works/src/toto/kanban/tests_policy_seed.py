"""The canonical ConsensusPolicy rows: seeded on every fresh database, and re-seedable.

kanban's hand-written migration `0002_seed_consensus_policies` seeds them (the
2026-10-01 reset kept that step on top of the fresh initial), and
`ingress_kanban` calls `work.seed_canonical_policies()` too, so the seed does
not live only in a migration.
"""

from django.test import TestCase

from toto.kanban import work
from toto.kanban.models import ConsensusPolicy


class PolicySeedTests(TestCase):
    def test_a_fresh_database_holds_the_three_policies(self):
        """The test database is built by migrating, so this is the migration's work."""
        self.assertEqual(
            sorted(ConsensusPolicy.objects.values_list("name", flat=True)),
            ["1 of 1", "2 of 3", "3 of 5"])
        self.assertEqual(
            list(ConsensusPolicy.objects.filter(is_default=True)
                 .values_list("name", flat=True)),
            ["1 of 1"])

    def test_the_seed_restores_deleted_rows_and_is_idempotent(self):
        ConsensusPolicy.objects.all().delete()
        work.seed_canonical_policies()
        work.seed_canonical_policies()
        self.assertEqual(ConsensusPolicy.objects.count(), 3)
        shapes = {p.name: (p.required_reviews, p.required_accepts, p.reject_threshold, p.is_default)
                  for p in ConsensusPolicy.objects.all()}
        self.assertEqual(shapes, {
            name: (reviews, accepts, rejects, is_default)
            for name, reviews, accepts, rejects, is_default in work.CANONICAL_POLICIES
        })
