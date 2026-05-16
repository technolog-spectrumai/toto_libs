"""
ravioli.scaffold — Django model introspection to draft graph YAML configs.

Rules:
  - Only models that inherit DomainEntity (detected by having a 'uid' UUIDField)
    are included as graph nodes.
  - Scalar, non-primary-key concrete fields become node properties.
  - ForeignKey / OneToOneField → cardinality: one link.
  - ManyToManyField → cardinality: many link.
  - A relational field is only included as a link when the related model also
    has a 'uid' field; otherwise it is silently skipped.
  - GIS geometry fields get  transform: wkt
  - ImageField / FileField   get  transform: file_url
  - JSONField                gets transform: json
  - All relation names (REL) are placeholder strings — the user must rename them.
"""

from django.apps import apps as django_apps
from django.db import models as dj_models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_uid(model):
    """Return True if the model exposes a 'uid' field (DomainEntity pattern)."""
    try:
        model._meta.get_field("uid")
        return True
    except Exception:
        return False


def _gis_geometry_types():
    try:
        from django.contrib.gis.db import models as gis
        return (
            gis.GeometryField,
            gis.PointField,
            gis.LineStringField,
            gis.MultiLineStringField,
            gis.PolygonField,
            gis.MultiPolygonField,
            gis.MultiPointField,
            gis.GeometryCollectionField,
        )
    except ImportError:
        return ()


def _field_transform(field):
    """
    Return the ravioli transform name for a concrete field, or None for a
    plain copy (no transform needed).
    """
    geo_types = _gis_geometry_types()
    if geo_types and isinstance(field, geo_types):
        return "wkt"
    if isinstance(field, (dj_models.ImageField, dj_models.FileField)):
        return "file_url"
    if isinstance(field, dj_models.JSONField):
        return "json"
    return None


def _is_property_field(field):
    """Return True if this field should appear as a node property."""
    if not getattr(field, "concrete", False):
        return False
    if field.primary_key:
        return False
    if field.is_relation:
        return False
    if field.name == "uid":
        return False  # the uuid_field is not a property
    return True


def _is_link_field(field):
    """Return True if this field should generate a graph link."""
    if not getattr(field, "concrete", False):
        return False
    if not field.is_relation:
        return False
    if not isinstance(field, (
        dj_models.ForeignKey,
        dj_models.OneToOneField,
        dj_models.ManyToManyField,
    )):
        return False
    related = field.related_model
    if related is None:
        return False
    return _has_uid(related)


# ---------------------------------------------------------------------------
# Node / link builders
# ---------------------------------------------------------------------------

def _build_node(model):
    label = model.__name__
    model_path = f"{model.__module__}.{model.__name__}"

    fields = {}
    for field in model._meta.get_fields():
        if not _is_property_field(field):
            continue
        transform = _field_transform(field)
        if transform is None:
            fields[field.name] = field.name  # simple: neo_prop: sql_field
        else:
            fields[field.name] = {"source": field.name, "transform": transform}

    entry = {
        "label": label,
        "model": model_path,
        "uuid_field": "uid",
    }
    if fields:
        entry["fields"] = fields
    return entry


def _build_link(from_label, field):
    related = field.related_model
    to_label = related.__name__

    if isinstance(field, dj_models.ManyToManyField):
        cardinality = "many"
        nullable = True
    else:
        cardinality = "one"
        nullable = bool(field.null)

    # Placeholder relation name — user must rename to something meaningful.
    relation = f"{from_label.upper()}_HAS_{to_label.upper()}"

    return {
        "from_label": from_label,
        "to_label": to_label,
        "relation": relation,
        "source": field.name,
        "cardinality": cardinality,
        "nullable": nullable,
    }


# ---------------------------------------------------------------------------
# Per-app scaffold
# ---------------------------------------------------------------------------

def scaffold_app(app_label):
    """
    Introspect one Django app and return a graph config dict ready for
    serialization, or None if the app has no graphable models.
    """
    app_config = django_apps.get_app_config(app_label)
    nodes = []
    links = []

    for model in app_config.get_models():
        if not _has_uid(model):
            continue

        node = _build_node(model)
        nodes.append(node)

        from_label = model.__name__
        for field in model._meta.get_fields():
            if _is_link_field(field):
                links.append(_build_link(from_label, field))

    if not nodes:
        return None

    return {
        "app": app_label,
        "nodes": nodes,
        "links": links,
    }


# ---------------------------------------------------------------------------
# YAML serialization
# ---------------------------------------------------------------------------

def _bool_str(v):
    return "true" if v else "false"


def config_to_yaml(config):
    """
    Render a config dict to a clean, human-readable YAML string.
    Uses hand-built output so formatting is predictable and reviewable.
    """
    lines = [
        "# Auto-generated by ravioli_scaffold — review and edit before committing.",
        "# * Rename every REL placeholder to a meaningful Cypher relationship type.",
        "# * Verify cross-app to_label values match labels in other YAML files.",
        "# * Remove fields / links that should not be part of the graph.",
        "",
        f"app: {config['app']}",
        "",
        "nodes:",
    ]

    for node in config.get("nodes", []):
        lines.append(f"  - label: {node['label']}")
        lines.append(f"    model: {node['model']}")
        lines.append(f"    uuid_field: {node['uuid_field']}")

        fields = node.get("fields", {})
        if fields:
            lines.append("    fields:")
            for neo_name, field_def in fields.items():
                if isinstance(field_def, dict):
                    lines.append(f"      {neo_name}:")
                    for k, v in field_def.items():
                        lines.append(f"        {k}: {v}")
                else:
                    lines.append(f"      {neo_name}: {field_def}")
        lines.append("")  # blank line between nodes

    lines.append("links:")

    if not config.get("links"):
        lines.append("  []")
    else:
        for link in config["links"]:
            lines.append(f"  - from_label: {link['from_label']}")
            lines.append(f"    to_label: {link['to_label']}")
            lines.append(f"    relation: {link['relation']}  # TODO: rename")
            lines.append(f"    source: {link['source']}")
            lines.append(f"    cardinality: {link['cardinality']}")
            lines.append(f"    nullable: {_bool_str(link['nullable'])}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
