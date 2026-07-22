"""Mapping-spec validation and record value resolution — pure, no I/O.

A ``mapping_spec`` declares how one extracted record becomes graph elements:

.. code-block:: jsonc

    {
      "version": 1,
      "nodes": [{
        "rule_id": "author",                            // unique [a-z0-9_-]+
        "category_slug": "person",                      // existing BentoCategory
        "identifier": {"path": "author.display_name"},  // display + dedupe surface
        "properties": {                                 // each value EXACTLY ONE of
          "name":   {"path": "author.display_name"},    //   dot-path into the record
          "source": {"const": "openalex"},              //   literal
          "note":   {"template": "{title} ({year})"}    //   "{path}" placeholders
        },
        "when": {"path": "author.display_name", "op": "truthy"}   // optional filter
      }],
      "relationships": [{
        "rule_id": "wrote",
        "edge_type_slug": "wrote",                      // existing BentoEdgeType
        "from_rule": "author", "to_rule": "work",       // node rule_ids, same record
        "properties": {"year": {"path": "publication_year"}},
        "when": null
      }]
    }

Dot-paths walk dicts by key and lists by numeric segment (``ids.0.value``); any
miss resolves to ``None`` rather than raising, so a sparse record simply yields
fewer values.
"""

import re

RULE_ID_RE = re.compile(r"^[a-z0-9_-]+$")
TEMPLATE_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")

VALUE_SOURCE_KEYS = ("path", "const", "template")
WHEN_OPS = ("truthy", "eq", "ne", "contains")
_OPS_NEEDING_VALUE = ("eq", "ne", "contains")


class MappingError(ValueError):
    pass


def get_path(obj, path):
    """Walk ``obj`` by a dot-path; numeric segments index lists. None on any miss."""
    if path is None:
        return None
    if path == "":
        return obj
    current = obj
    for segment in str(path).split("."):
        if isinstance(current, dict):
            if segment not in current:
                return None
            current = current[segment]
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def resolve_value(record, source):
    """Resolve one value-source dict (exactly one of path/const/template) on a record.

    ``template`` returns None when any placeholder is missing — a half-filled
    string like ``"Ada (None)"`` would silently poison graph properties.
    """
    if not isinstance(source, dict):
        return None
    if "const" in source:
        return source["const"]
    if "path" in source:
        return get_path(record, source["path"])
    if "template" in source:
        template = str(source["template"])
        missing = False

        def _sub(match):
            nonlocal missing
            value = get_path(record, match.group(1).strip())
            if value is None:
                missing = True
                return ""
            return str(value)

        rendered = TEMPLATE_PLACEHOLDER_RE.sub(_sub, template)
        return None if missing else rendered
    return None


def check_when(record, when):
    """Evaluate an optional ``when`` filter; ``None``/empty means "always"."""
    if not when:
        return True
    value = get_path(record, when.get("path"))
    op = when.get("op") or "truthy"
    expected = when.get("value")
    if op == "truthy":
        return bool(value)
    if op == "eq":
        return value == expected
    if op == "ne":
        return value != expected
    if op == "contains":
        try:
            return expected in value
        except TypeError:
            return False
    return False


# ---------------------------------------------------------------------------
# spec validation
# ---------------------------------------------------------------------------

def _value_source_errors(source, where):
    errors = []
    if not isinstance(source, dict):
        return [f"{where}: value source must be an object, got {type(source).__name__}."]
    keys = [k for k in VALUE_SOURCE_KEYS if k in source]
    if len(keys) != 1:
        errors.append(
            f"{where}: value source must have exactly one of {', '.join(VALUE_SOURCE_KEYS)}."
        )
        return errors
    key = keys[0]
    if key in ("path", "template") and not isinstance(source[key], str):
        errors.append(f"{where}: '{key}' must be a string.")
    return errors


def _when_errors(when, where):
    if when in (None, {}):
        return []
    if not isinstance(when, dict):
        return [f"{where}: 'when' must be an object."]
    errors = []
    if not isinstance(when.get("path"), str) or not when.get("path"):
        errors.append(f"{where}: 'when' requires a string 'path'.")
    op = when.get("op") or "truthy"
    if op not in WHEN_OPS:
        errors.append(f"{where}: 'when' op must be one of {', '.join(WHEN_OPS)}.")
    elif op in _OPS_NEEDING_VALUE and "value" not in when:
        errors.append(f"{where}: 'when' op '{op}' requires a 'value'.")
    return errors


def _properties_errors(properties, where):
    if properties in (None, {}):
        return []
    if not isinstance(properties, dict):
        return [f"{where}: 'properties' must be an object."]
    errors = []
    for prop, source in properties.items():
        errors += _value_source_errors(source, f"{where} property '{prop}'")
    return errors


def validate_mapping_spec(spec, *, check_bento=True):
    """Return a list of error strings (empty = valid).

    Structural checks are always run. With ``check_bento`` the spec is also
    checked against the live Bento templates (needs the DB): category/edge-type
    slugs must exist, mapped node properties must be declared in the category's
    ``property_schema``, and relationship endpoint categories must be permitted
    by the edge type's ``allowed_sources``/``allowed_targets`` (empty = any).
    """
    if not isinstance(spec, dict):
        return ["mapping_spec must be a JSON object."]
    errors = []
    version = spec.get("version", 1)
    if version != 1:
        errors.append(f"Unsupported mapping_spec version {version!r} (expected 1).")

    node_rules = spec.get("nodes")
    if not isinstance(node_rules, list) or not node_rules:
        errors.append("mapping_spec.nodes must be a non-empty list.")
        node_rules = []
    rel_rules = spec.get("relationships") or []
    if not isinstance(rel_rules, list):
        errors.append("mapping_spec.relationships must be a list.")
        rel_rules = []

    rule_categories = {}
    for i, rule in enumerate(node_rules):
        where = f"nodes[{i}]"
        if not isinstance(rule, dict):
            errors.append(f"{where}: must be an object.")
            continue
        rule_id = rule.get("rule_id")
        if not isinstance(rule_id, str) or not RULE_ID_RE.match(rule_id):
            errors.append(f"{where}: rule_id must match [a-z0-9_-]+.")
        elif rule_id in rule_categories:
            errors.append(f"{where}: duplicate rule_id '{rule_id}'.")
        else:
            rule_categories[rule_id] = rule.get("category_slug")
        if not isinstance(rule.get("category_slug"), str) or not rule.get("category_slug"):
            errors.append(f"{where}: category_slug is required.")
        identifier = rule.get("identifier")
        if not isinstance(identifier, dict) or not isinstance(identifier.get("path"), str):
            errors.append(f"{where}: identifier requires a string 'path'.")
        errors += _properties_errors(rule.get("properties"), where)
        errors += _when_errors(rule.get("when"), where)

    seen_rel_ids = set()
    for i, rule in enumerate(rel_rules):
        where = f"relationships[{i}]"
        if not isinstance(rule, dict):
            errors.append(f"{where}: must be an object.")
            continue
        rule_id = rule.get("rule_id")
        if not isinstance(rule_id, str) or not RULE_ID_RE.match(rule_id):
            errors.append(f"{where}: rule_id must match [a-z0-9_-]+.")
        elif rule_id in seen_rel_ids or rule_id in rule_categories:
            errors.append(f"{where}: duplicate rule_id '{rule_id}'.")
        else:
            seen_rel_ids.add(rule_id)
        if not isinstance(rule.get("edge_type_slug"), str) or not rule.get("edge_type_slug"):
            errors.append(f"{where}: edge_type_slug is required.")
        for endpoint in ("from_rule", "to_rule"):
            if rule.get(endpoint) not in rule_categories:
                errors.append(f"{where}: {endpoint} must name a node rule_id.")
        errors += _properties_errors(rule.get("properties"), where)
        errors += _when_errors(rule.get("when"), where)

    if errors or not check_bento:
        return errors
    return errors + _bento_errors(node_rules, rel_rules, rule_categories)


def _bento_errors(node_rules, rel_rules, rule_categories):
    """Spec vs live Bento templates. Requires the SQL DB (not Neo4j)."""
    from toto.bento.models import BentoCategory, BentoEdgeType

    errors = []
    categories = {c.slug: c for c in BentoCategory.objects.filter(internal=False)}
    edge_types = {
        et.slug: et
        for et in BentoEdgeType.objects.prefetch_related("allowed_sources", "allowed_targets")
    }

    for i, rule in enumerate(node_rules):
        where = f"nodes[{i}]"
        slug = rule.get("category_slug")
        cat = categories.get(slug)
        if cat is None:
            errors.append(f"{where}: unknown category '{slug}'.")
            continue
        declared = {f.get("name") for f in (cat.property_schema or [])}
        for prop in (rule.get("properties") or {}):
            if prop not in declared:
                errors.append(
                    f"{where}: property '{prop}' is not declared on category '{slug}'."
                )

    for i, rule in enumerate(rel_rules):
        where = f"relationships[{i}]"
        slug = rule.get("edge_type_slug")
        et = edge_types.get(slug)
        if et is None:
            errors.append(f"{where}: unknown edge type '{slug}'.")
            continue
        allowed_src = [c.slug for c in et.allowed_sources.all()]
        allowed_tgt = [c.slug for c in et.allowed_targets.all()]
        declared = {f.get("name") for f in (et.property_schema or [])}
        for prop in (rule.get("properties") or {}):
            if prop not in declared:
                errors.append(
                    f"{where}: property '{prop}' is not declared on edge type '{slug}'."
                )
        src_cat = rule_categories.get(rule.get("from_rule"))
        tgt_cat = rule_categories.get(rule.get("to_rule"))
        if allowed_src and src_cat not in allowed_src:
            errors.append(
                f"{where}: '{src_cat}' is not an allowed source for '{slug}'."
            )
        if allowed_tgt and tgt_cat not in allowed_tgt:
            errors.append(
                f"{where}: '{tgt_cat}' is not an allowed target for '{slug}'."
            )
    return errors
