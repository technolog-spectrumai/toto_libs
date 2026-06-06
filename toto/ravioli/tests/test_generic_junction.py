"""Projection of a `generic: true` junction link (bento.SubjectReference).

SubjectReference is a polymorphic edge (IdeaBox → one of the allowed external
models via a GenericForeignKey). The projector must resolve the target's graph
label per-row and must NOT select_related the GenericForeignKey field.

Requires toto.ravioli in INSTALLED_APPS (BUILD_NEO4J=1); uses a fake Neo4j
client so no live database is needed.
"""
from __future__ import annotations

import unittest

from django.apps import apps
from django.test import TestCase

_RAVIOLI_INSTALLED = apps.is_installed("toto.ravioli")


class FakeNeo4jClient:
    def __init__(self):
        self.calls = []

    def run_cypher(self, query, params=None):
        self.calls.append((query, params or {}))
        return []


@unittest.skipUnless(_RAVIOLI_INSTALLED, "requires BUILD_NEO4J=1")
class GenericJunctionProjectionTests(TestCase):
    def setUp(self):
        from django.contrib.contenttypes.models import ContentType
        from toto.bento.models import IdeaBox, SubjectReference
        from toto.events.models import EventCategory

        self.box = IdeaBox.objects.create(label="Anchor")
        self.category = EventCategory.objects.create(name="Workshops")
        ct = ContentType.objects.get_for_model(EventCategory)
        SubjectReference.objects.create(
            box=self.box, content_type=ct, object_id=str(self.category.pk), label="about",
        )

    def _references_link(self, runner):
        for link_def in runner.link_defs():
            if link_def.get("relation") == "REFERENCES" and link_def.get("generic"):
                return link_def
        return None

    def test_generic_junction_projects_to_resolved_label(self):
        from toto.ravioli.loader import load_all_configs
        from toto.ravioli.projection import ProjectionRunner

        client = FakeNeo4jClient()
        runner = ProjectionRunner(client, load_all_configs())
        link_def = self._references_link(runner)
        self.assertIsNotNone(link_def, "REFERENCES generic link missing from bento.yaml")

        # Must not raise — a GenericForeignKey can't be select_related.
        runner._project_link(link_def)

        creates = [c for c in client.calls if "CREATE (a)-[r:REFERENCES" in c[0]]
        self.assertEqual(len(creates), 1)
        query, params = creates[0]
        self.assertIn("MATCH (b:EventCategory", query)  # label resolved from the GFK target
        self.assertEqual(params["f"], str(self.box.uid))
        self.assertEqual(params["t"], str(self.category.uid))

    def test_generic_link_validates(self):
        from toto.ravioli.loader import load_all_configs, validate_configs

        # The generic REFERENCES link (no fixed to_label) must not raise validation errors.
        errors = [e for e in validate_configs(load_all_configs()) if "bento" in e or "REFERENCES" in e]
        self.assertEqual(errors, [])
