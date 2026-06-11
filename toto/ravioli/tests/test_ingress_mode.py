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
