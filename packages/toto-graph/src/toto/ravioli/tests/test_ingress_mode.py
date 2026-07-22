"""Rich vs. thin ingress (RAVIOLI_RICH_INGRESS) for ravioli + bento.

Thin (the default) skips all heavy Neo4j seeding; rich attempts it. These tests
never require a live Neo4j — the rich path is exercised with RAVIOLI_ENABLED
False so it short-circuits before connecting.
"""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings


def _run(cmd, **opts):
    out = StringIO()
    call_command(cmd, stdout=out, stderr=out, **opts)
    return out.getvalue()


class RavioliIngressModeTests(TestCase):
    @override_settings(RAVIOLI_RICH_INGRESS=False)
    def test_thin_skips_sample_graph(self):
        output = _run("ingress_ravioli")
        self.assertIn("Thin ingress", output)
        self.assertNotIn("Seeded sample graph", output)

    @override_settings(RAVIOLI_ENABLED=False)
    def test_rich_reaches_seed_but_skips_when_neo4j_disabled(self):
        # rich=True drives the seed path; RAVIOLI_ENABLED=False makes it skip
        # before any Neo4j connection is attempted.
        output = _run("ingress_ravioli", rich=True)
        self.assertIn("Neo4j disabled", output)

    def test_default_is_thin(self):
        # No override: settings default (RAVIOLI_RICH_INGRESS unset → thin).
        output = _run("ingress_ravioli")
        self.assertIn("Thin ingress", output)


class BentoIngressModeTests(TestCase):
    def test_thin_seeds_templates_but_skips_graph(self):
        output = _run("ingress_bento", full=True, rich=False)
        self.assertIn("Bento template seeding complete", output)
        self.assertIn("skipping Neo4j graph seeding", output)
        # SQL-side templates are still created.
        from toto.bento.models import BentoCategory
        self.assertTrue(BentoCategory.objects.exists())


class BentoSeedGraphTypesTests(TestCase):
    """The SEED_GRAPH_TYPES flag seeds concept/note/references in every mode."""

    def test_off_by_default(self):
        from toto.bento.models import BentoCategory, BentoEdgeType

        _run("ingress_bento")  # non-full, flag unset
        self.assertFalse(BentoCategory.objects.filter(slug="concept").exists())
        self.assertFalse(BentoCategory.objects.filter(slug="note").exists())
        self.assertFalse(BentoEdgeType.objects.filter(slug="references").exists())

    @override_settings(SEED_GRAPH_TYPES=True)
    def test_setting_seeds_in_non_full_mode(self):
        from toto.bento.models import BentoCategory, BentoEdgeType

        _run("ingress_bento")  # non-full — the types still get seeded
        concept = BentoCategory.objects.get(slug="concept")
        note = BentoCategory.objects.get(slug="note")
        # concept/note declare `name` (the identifier) so the ingestor stores it
        # top-level (not in `extra`); both are LLM-visible (not internal).
        self.assertEqual([f["name"] for f in concept.property_schema], ["name", "body"])
        self.assertFalse(concept.internal)
        self.assertFalse(note.internal)
        et = BentoEdgeType.objects.get(slug="references")
        self.assertEqual(et.rel_type, "REFERENCES")
        # "references" links note → concept.
        self.assertEqual([c.slug for c in et.allowed_sources.all()], ["note"])
        self.assertEqual([c.slug for c in et.allowed_targets.all()], ["concept"])
        self.assertIn(concept, et.allowed_targets.all())
        self.assertIn(note, et.allowed_sources.all())

    def test_cli_flag_overrides_setting(self):
        from toto.bento.models import BentoCategory

        # Flag forces seeding on even though the setting defaults off.
        _run("ingress_bento", seed_graph_types=True)
        self.assertTrue(BentoCategory.objects.filter(slug="concept").exists())

    @override_settings(SEED_GRAPH_TYPES=True)
    def test_idempotent(self):
        from toto.bento.models import BentoCategory, BentoEdgeType

        _run("ingress_bento")
        _run("ingress_bento")
        self.assertEqual(BentoCategory.objects.filter(slug="concept").count(), 1)
        self.assertEqual(BentoEdgeType.objects.filter(slug="references").count(), 1)


class BentoSyncedTemplatesTests(TestCase):
    """Non-full bento ingress derives categories/edge-types from sql_neo4j_sync."""

    def test_non_full_seeds_synced_templates(self):
        from toto.bento.models import BentoCategory, BentoEdgeType

        _run("ingress_bento")  # non-full default — no Neo4j, no demo graph

        # Node categories mirror sql_neo4j_sync graph labels.
        for label in ("KanbanTask", "Person", "Event"):
            self.assertTrue(BentoCategory.objects.filter(neo4j_label=label).exists(), label)

        # property_schema is derived from the YAML field map (+ transforms).
        task = BentoCategory.objects.get(neo4j_label="KanbanTask")
        types = {f["name"]: f["type"] for f in task.property_schema}
        self.assertEqual(types.get("title"), "string")
        self.assertEqual(types.get("metadata"), "json")   # default_dict transform

        # Edge types carry allowed source/target categories.
        et = BentoEdgeType.objects.filter(rel_type="ASSIGNED_TO").first()
        self.assertIsNotNone(et)
        self.assertIn("KanbanTask", [c.neo4j_label for c in et.allowed_sources.all()])
        self.assertIn("Person", [c.neo4j_label for c in et.allowed_targets.all()])

        # "Used when syncing": bento recognises a synced node by its label, so
        # nodes the sync writes with this label are now editable as this category.
        from toto.bento import graph_service as gs
        self.assertIn("KanbanTask", gs._bento_labels())

    def test_idempotent(self):
        from toto.bento.models import BentoCategory

        _run("ingress_bento")
        _run("ingress_bento")
        self.assertEqual(BentoCategory.objects.filter(neo4j_label="KanbanTask").count(), 1)
