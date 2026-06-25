"""Bento SQL models — **templates only**.

Bento's source of truth is Neo4j: the actual nodes and relationships live in the
graph. SQL keeps only the *templates* that describe their shape:

* :class:`BentoCategory`  → a node-type template (one Neo4j label + a property schema)
* :class:`BentoEdgeType`  → an edge-type template (one relationship type + allowed
  source/target categories + a property schema)

From these rows, :mod:`toto.bento.registry` builds neomodel ``StructuredNode`` /
``StructuredRel`` classes on the fly, and :mod:`toto.bento.graph_service` performs
all graph CRUD. Nothing in this module touches Neo4j.
"""

import re

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify

from toto.core.domain import DomainEntity

# Property types a template may declare. These map to neomodel descriptors in
# toto.bento.registry.PROP_TYPES — keep the two in sync.
ALLOWED_PROP_TYPES = {
    "string",
    "text",
    "integer",
    "float",
    "boolean",
    "datetime",
    "json",
}

# Names that collide with structural node/edge attributes and may not be used as
# template property names.
RESERVED_PROP_NAMES = {"uid", "extra", "id", "element_id"}

LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REL_TYPE_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
PROP_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_property_schema(schema):
    """Return a list of human-readable errors for a ``property_schema`` value.

    A schema is a list of field definitions::

        [{"name": "title", "type": "string", "required": true,
          "label": "Title", "help": "..."}]

    Empty list (no errors) means the schema is valid.
    """
    errors = []
    if schema in (None, ""):
        return errors
    if not isinstance(schema, list):
        return ["property_schema must be a list of field definitions."]

    seen = set()
    for i, field in enumerate(schema):
        if not isinstance(field, dict):
            errors.append(f"property_schema[{i}] must be an object.")
            continue
        name = field.get("name")
        ftype = field.get("type")
        if not isinstance(name, str) or not PROP_NAME_RE.match(name or ""):
            errors.append(f"property_schema[{i}]: invalid property name {name!r}.")
            continue
        if name in RESERVED_PROP_NAMES:
            errors.append(f"property_schema[{i}]: '{name}' is a reserved name.")
        if name in seen:
            errors.append(f"property_schema[{i}]: duplicate property '{name}'.")
        seen.add(name)
        if ftype not in ALLOWED_PROP_TYPES:
            errors.append(
                f"property_schema[{i}]: type {ftype!r} not in "
                f"{sorted(ALLOWED_PROP_TYPES)}."
            )
    return errors


def validate_searchable_properties(searchable, schema):
    """Return a list of errors for a ``searchable_properties`` value.

    Each entry must be a string naming a field declared in ``property_schema``.
    Empty/None is valid (detection falls back to name/title/label).
    """
    errors = []
    if searchable in (None, "", []):
        return errors
    if not isinstance(searchable, list):
        return ["searchable_properties must be a list of property names."]
    declared = {
        f.get("name")
        for f in (schema or [])
        if isinstance(f, dict) and f.get("name")
    }
    for i, name in enumerate(searchable):
        if not isinstance(name, str) or not name:
            errors.append(f"searchable_properties[{i}] must be a property name.")
        elif declared and name not in declared:
            errors.append(
                f"searchable_properties[{i}]: '{name}' is not declared in "
                "property_schema."
            )
    return errors


def default_label(name):
    """Derive a CamelCase Neo4j label from a display name."""
    parts = re.split(r"[^A-Za-z0-9]+", name or "")
    label = "".join(p[:1].upper() + p[1:] for p in parts if p)
    if not label or not label[0].isalpha():
        label = "Node" + label
    return label or "Node"


def default_rel_type(name):
    """Derive an UPPER_SNAKE Neo4j relationship type from a display name."""
    rel = re.sub(r"[^A-Za-z0-9]+", "_", (name or "").strip()).strip("_").upper()
    if not rel or not REL_TYPE_RE.match(rel):
        rel = ("REL_" + rel) if rel else "REL"
    return rel


class BentoCategory(DomainEntity):
    """Node-type template: defines one class of Neo4j node."""

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField(blank=True)

    neo4j_label = models.CharField(
        max_length=120,
        blank=True,
        help_text="Neo4j node label for nodes of this category, e.g. 'Idea'.",
    )
    property_schema = models.JSONField(
        default=list,
        blank=True,
        help_text="List of {name, type, required, label, help} field definitions.",
    )
    searchable_properties = models.JSONField(
        default=list,
        blank=True,
        help_text="Property names (declared in property_schema) whose values "
        "identify a node in free text — used by the Ingestor to detect "
        "existing nodes of this category. Empty falls back to "
        "name/title/label by convention.",
    )
    alias_property = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Optional property holding alternate surface forms for a node "
        "(a JSON list or a comma-separated string). Also fed to detection.",
    )
    internal = models.BooleanField(
        default=False,
        help_text="Hide this category from the Ingestor's LLM category set "
        "(e.g. SQL-sync infrastructure types). Does not affect manual editing.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "node category template"
        verbose_name_plural = "node category templates"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        if not self.neo4j_label:
            self.neo4j_label = default_label(self.name)
        super().save(*args, **kwargs)

    def clean(self):
        errors = {}
        if self.neo4j_label and not LABEL_RE.match(self.neo4j_label):
            errors["neo4j_label"] = (
                "Must start with a letter or underscore and contain only "
                "letters, digits, and underscores."
            )
        schema_errors = validate_property_schema(self.property_schema)
        if schema_errors:
            errors["property_schema"] = schema_errors
        search_errors = validate_searchable_properties(
            self.searchable_properties, self.property_schema
        )
        if search_errors:
            errors["searchable_properties"] = search_errors
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.name


class BentoEdgeType(DomainEntity):
    """Edge-type template: defines one class of Neo4j relationship."""

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField(blank=True)

    rel_type = models.CharField(
        max_length=120,
        blank=True,
        help_text="Neo4j relationship type, e.g. 'SUPPORTS' (UPPER_SNAKE_CASE).",
    )
    allowed_sources = models.ManyToManyField(
        BentoCategory,
        blank=True,
        related_name="outgoing_edge_types",
        help_text="Categories whose nodes may be the source of this edge. "
        "Empty means any.",
    )
    allowed_targets = models.ManyToManyField(
        BentoCategory,
        blank=True,
        related_name="incoming_edge_types",
        help_text="Categories whose nodes may be the target of this edge. "
        "Empty means any.",
    )
    property_schema = models.JSONField(default=list, blank=True)
    directed = models.BooleanField(default=True)
    trigger_lemmas = models.JSONField(
        default=list,
        blank=True,
        help_text="Optional lemma triggers (e.g. ['support', 'back']). When one "
        "appears in a sentence between two endpoints whose categories match "
        "this edge, the Ingestor proposes this relationship and raises its "
        "confidence.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "edge type template"
        verbose_name_plural = "edge type templates"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        if not self.rel_type:
            self.rel_type = default_rel_type(self.name)
        super().save(*args, **kwargs)

    def clean(self):
        errors = {}
        if self.rel_type and not REL_TYPE_RE.match(self.rel_type):
            errors["rel_type"] = (
                "Must be UPPER_SNAKE_CASE: start with a letter or underscore and "
                "contain only A-Z, 0-9, and underscores."
            )
        schema_errors = validate_property_schema(self.property_schema)
        if schema_errors:
            errors["property_schema"] = schema_errors
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.name
