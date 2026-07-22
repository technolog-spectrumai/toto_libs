"""Dynamic neomodel class registry, built from Bento SQL templates.

Each :class:`~toto.bento.models.BentoCategory` row becomes a neomodel
``StructuredNode`` subclass; each :class:`~toto.bento.models.BentoEdgeType` row
becomes a ``StructuredRel`` subclass. Template definitions are treated as **data
only** — nothing here ever ``eval``/``exec``s template content; property names,
labels and relationship types are validated by the models' ``clean()`` before
they ever reach this module.

Classes are cached per slug and keyed on the template's ``updated_at`` so that
(a) rebuilding is deterministic and identical across workers and (b) we reuse
the same class object until the template actually changes. The cache is cleared
by :func:`invalidate` (wired to template ``post_save``/``post_delete`` signals
in :mod:`toto.bento.signals`).

neomodel is imported lazily so this module stays importable when Neo4j is
disabled (class *definition* needs no connection; only queries do).
"""

import re

# slug -> (updated_at, class)
_NODE_CACHE: dict = {}
_EDGE_CACHE: dict = {}

# Names that collide with structural attributes and may never be template props.
RESERVED = {"uid", "extra", "id", "element_id"}


def _descriptor(ftype, required):
    """Return a fresh neomodel property descriptor for a template field type."""
    from neomodel import (
        BooleanProperty,
        DateTimeProperty,
        FloatProperty,
        IntegerProperty,
        JSONProperty,
        StringProperty,
    )

    mapping = {
        "string": StringProperty,
        "text": StringProperty,
        "integer": IntegerProperty,
        "float": FloatProperty,
        "boolean": BooleanProperty,
        "datetime": DateTimeProperty,
        "json": JSONProperty,
    }
    cls = mapping.get(ftype, StringProperty)
    return cls(required=bool(required))


def _class_name(prefix, slug):
    ident = re.sub(r"[^0-9a-zA-Z_]", "_", slug or "")
    if not ident or not ident[0].isalpha():
        ident = "x" + ident
    return f"{prefix}_{ident}"


def _typed_attrs(schema):
    """Build the {name: descriptor} mapping shared by nodes and edges."""
    from neomodel import JSONProperty

    attrs = {"extra": JSONProperty(default=dict)}
    for field in schema or []:
        name = field.get("name") if isinstance(field, dict) else None
        if not name or name in RESERVED or name in attrs:
            continue
        attrs[name] = _descriptor(field.get("type"), field.get("required"))
    return attrs


def _unregister_label(label):
    """Best-effort removal of a label from neomodel's class registry.

    Rebuilding a class for the same label otherwise triggers neomodel's
    "already defined" warning. Safe across neomodel versions / when no
    connection exists.
    """
    try:
        from neomodel import db

        registry = getattr(db, "_NODE_CLASS_REGISTRY", None)
        if not isinstance(registry, dict):
            return
        for key in list(registry.keys()):
            try:
                if label in key:
                    del registry[key]
            except TypeError:
                continue
    except Exception:
        pass


def build_node_class(category):
    """Construct (uncached) a neomodel StructuredNode subclass for a category."""
    from neomodel import StructuredNode, UniqueIdProperty

    attrs = _typed_attrs(category.property_schema)
    attrs["uid"] = UniqueIdProperty()
    attrs["__label__"] = category.neo4j_label
    _unregister_label(category.neo4j_label)
    return type(_class_name("BentoNode", category.slug), (StructuredNode,), attrs)


def build_edge_class(edge_type):
    """Construct (uncached) a neomodel StructuredRel subclass for an edge type."""
    from neomodel import StructuredRel

    attrs = _typed_attrs(edge_type.property_schema)
    return type(_class_name("BentoEdge", edge_type.slug), (StructuredRel,), attrs)


# --------------------------------------------------------------------------
# cached accessors
# --------------------------------------------------------------------------

def node_class_for(category):
    cached = _NODE_CACHE.get(category.slug)
    if cached and cached[0] == category.updated_at:
        return cached[1]
    klass = build_node_class(category)
    _NODE_CACHE[category.slug] = (category.updated_at, klass)
    return klass


def edge_class_for(edge_type):
    cached = _EDGE_CACHE.get(edge_type.slug)
    if cached and cached[0] == edge_type.updated_at:
        return cached[1]
    klass = build_edge_class(edge_type)
    _EDGE_CACHE[edge_type.slug] = (edge_type.updated_at, klass)
    return klass


def node_class(slug):
    """Look up a category by slug and return its (cached) neomodel class."""
    from .models import BentoCategory

    cat = BentoCategory.objects.filter(slug=slug).first()
    return node_class_for(cat) if cat else None


def edge_class(slug):
    from .models import BentoEdgeType

    et = BentoEdgeType.objects.filter(slug=slug).first()
    return edge_class_for(et) if et else None


def invalidate(slug=None):
    """Drop cached classes. Called by template change signals."""
    if slug is None:
        _NODE_CACHE.clear()
        _EDGE_CACHE.clear()
    else:
        _NODE_CACHE.pop(slug, None)
        _EDGE_CACHE.pop(slug, None)


def rebuild():
    """Clear the cache and eagerly rebuild every node/edge class from the DB."""
    from .models import BentoCategory, BentoEdgeType

    invalidate()
    for cat in BentoCategory.objects.all():
        node_class_for(cat)
    for et in BentoEdgeType.objects.all():
        edge_class_for(et)
