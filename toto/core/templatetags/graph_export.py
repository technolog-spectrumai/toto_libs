"""Shared ``{% export_to_graph_button %}`` inclusion tag.

Lives in ``toto.core`` (always installed) rather than ``toto.sql_neo4j_sync``
so detail templates can ``{% load graph_export %}`` even in builds without
Neo4j. When the graph layer is absent or disabled the tag renders nothing.
"""

from django import template
from django.conf import settings

register = template.Library()


def _label_for(model):
    """Resolve (label, uuid_field) for a model, or None if graph is unavailable."""
    try:
        from toto.sql_neo4j_sync.loader import label_for_model
    except ImportError:
        return None  # BUILD_NEO4J off — sql_neo4j_sync isn't installed.
    try:
        return label_for_model(model)
    except Exception:
        return None


@register.inclusion_tag("core/graph_export_button.html", takes_context=True)
def export_to_graph_button(context, obj, css_class=""):
    """Render an 'Export to graph' button for a graph-mapped model instance.

    Renders nothing when Neo4j is disabled, the object is missing, or its model
    is not declared in any graph/*.yaml config.
    """
    if obj is None or not getattr(settings, "RAVIOLI_ENABLED", False):
        return {"show": False}

    mapping = _label_for(type(obj))
    if mapping is None:
        return {"show": False}

    label, uuid_field = mapping
    meta = obj._meta
    return {
        "show": True,
        "csrf_token": context.get("csrf_token"),
        "app_label": meta.app_label,
        "model_name": meta.model_name,
        "object_uuid": getattr(obj, uuid_field),
        "graph_label": label,
        "css_class": css_class,
    }
