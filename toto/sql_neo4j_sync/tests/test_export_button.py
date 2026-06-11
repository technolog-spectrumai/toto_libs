"""Tests for the model→graph-label registry and the shared export-button tag.

The export *engine* (1-hop slice, diff, history-preserving apply) lives in
``toto.ravioli`` and is tested in ``ravioli/tests/test_graph_export.py``.
"""

from django.contrib.auth.models import User
from django.test import SimpleTestCase, override_settings


class LabelForModelTests(SimpleTestCase):
    def test_mapped_model_resolves_to_label(self):
        from toto.events.models import ScheduledEvent
        from toto.sql_neo4j_sync.loader import build_label_by_model

        mapping = build_label_by_model()
        self.assertEqual(mapping.get(ScheduledEvent), ("Event", "uid"))

    def test_unmapped_model_returns_none(self):
        from toto.sql_neo4j_sync.loader import build_label_by_model

        self.assertIsNone(build_label_by_model().get(User))


class ExportButtonTagTests(SimpleTestCase):
    def _render(self, obj):
        from toto.core.templatetags.graph_export import export_to_graph_button

        return export_to_graph_button(obj)

    def test_hidden_when_object_is_none(self):
        self.assertEqual(self._render(None), {"show": False})

    @override_settings(RAVIOLI_ENABLED=False)
    def test_hidden_when_graph_disabled(self):
        self.assertEqual(self._render(User()), {"show": False})

    @override_settings(RAVIOLI_ENABLED=True)
    def test_hidden_for_unmapped_model(self):
        self.assertEqual(self._render(User()), {"show": False})

    @override_settings(RAVIOLI_ENABLED=True)
    def test_shown_for_mapped_model(self):
        from toto.events.models import ScheduledEvent

        ctx = self._render(ScheduledEvent())
        self.assertTrue(ctx["show"])
        self.assertEqual(ctx["graph_label"], "Event")
        self.assertEqual(ctx["app_label"], "events")
        self.assertEqual(ctx["model_name"], "scheduledevent")
