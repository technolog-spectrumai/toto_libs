"""Shared ``{% export_to_graph_button %}`` inclusion tag.

Lives in ``toto.core`` (always installed) rather than the Neo4j apps so detail
templates can ``{% load graph_export %}`` even in builds without Neo4j. When the
graph layer is absent or disabled, the tag renders nothing. Otherwise it links
to ravioli's export *preview* page (the diff is reviewed there before applying).
"""

from django import template
from django.conf import settings

register = template.Library()


def _resolve(obj):
    """Return (label, uuid_field) for a graph-mapped, exportable model, else None."""
    try:
        from toto.sql_neo4j_sync.loader import label_for_model
    except ImportError:
        return None  # BUILD_NEO4J off — the graph apps aren't installed.

    try:
        from toto.ravioli.graph_export import is_app_excluded
    except ImportError:
        def is_app_excluded(_app):
            return False

    if is_app_excluded(obj._meta.app_label):
        return None  # workflows / file services / vault files are never exported.
    try:
        return label_for_model(type(obj))
    except Exception:
        return None


@register.inclusion_tag("core/graph_export_button.html")
def export_to_graph_button(obj, css_class=""):
    """Render an 'Export to graph' button for a graph-mapped model instance.

    Renders nothing when Neo4j is disabled, the object is missing, or its model
    is not declared in any graph/*.yaml config.
    """
    if obj is None or not getattr(settings, "RAVIOLI_ENABLED", False):
        return {"show": False}

    mapping = _resolve(obj)
    if mapping is None:
        return {"show": False}

    label, uuid_field = mapping
    meta = obj._meta
    return {
        "show": True,
        "app_label": meta.app_label,
        "model_name": meta.model_name,
        "object_uuid": getattr(obj, uuid_field),
        "graph_label": label,
        "css_class": css_class,
    }
