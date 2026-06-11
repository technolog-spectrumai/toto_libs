"""
ravioli.loader — YAML graph config loading and validation.

No Neo4j imports here. This module is safe to run without a live database.
"""

import importlib
from functools import lru_cache
from pathlib import Path

import yaml
from django.apps import apps as django_apps


GRAPH_DIR = Path(__file__).parent / "graph"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_all_configs():
    """Load *.yaml files in ravioli/graph/, skipping configs for uninstalled apps."""
    configs = []
    for path in sorted(GRAPH_DIR.glob("*.yaml")):
        with open(path) as f:
            config = yaml.safe_load(f) or {}
        app_label = config.get("app", "")
        if app_label:
            try:
                django_apps.get_app_config(app_label)
            except LookupError:
                continue
        config["_source"] = path.name
        configs.append(config)
    return configs


def grouped_models(configs=None):
    """Return {app_label: [label, ...]} for the admin / views layer."""
    if configs is None:
        configs = load_all_configs()
    result = {}
    for config in configs:
        app = config.get("app", "")
        result[app] = [node["label"] for node in config.get("nodes", [])]
    return result


# ---------------------------------------------------------------------------
# Model import helper
# ---------------------------------------------------------------------------

def import_model(model_path):
    """Import a Django model from a dotted path like 'toto.app.models.MyModel'."""
    try:
        module_path, class_name = model_path.rsplit(".", 1)
    except ValueError:
        raise ValueError(f"Invalid model path: {model_path!r}")
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


# ---------------------------------------------------------------------------
# Model → graph-label lookup (used by the per-object "Export to graph" button)
# ---------------------------------------------------------------------------

def build_label_by_model(configs=None):
    """Return ``{model_class: (label, uuid_field)}`` for every projected node."""
    if configs is None:
        configs = load_all_configs()
    mapping = {}
    for config in configs:
        for node in config.get("nodes", []):
            try:
                model = import_model(node["model"])
            except Exception:
                continue
            mapping[model] = (node["label"], node.get("uuid_field", "uid"))
    return mapping


@lru_cache(maxsize=1)
def _cached_label_by_model():
    # Graph configs are static YAML files read once per process.
    return build_label_by_model(load_all_configs())


def label_for_model(model, configs=None):
    """Return ``(label, uuid_field)`` for a Django model class, or None.

    None means the model is not declared in any graph/*.yaml — i.e. it has no
    graph representation and cannot be exported.
    """
    if configs is not None:
        return build_label_by_model(configs).get(model)
    return _cached_label_by_model().get(model)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_configs(configs):
    """
    Validate all loaded configs.  Returns a list of error strings.
    No Neo4j connection required — only Django model imports are checked.
    """
    errors = []

    # First pass: collect every declared node label so cross-file
    # references (e.g. locations → Person from socialhub) can be resolved.
    declared_labels: dict[str, tuple[str, dict]] = {}
    for config in configs:
        source = config.get("_source", "?")
        for node in config.get("nodes", []):
            label = node.get("label", "")
            if not label:
                errors.append(f"{source}: node entry missing 'label'")
                continue
            if label in declared_labels:
                first_source = declared_labels[label][0]
                errors.append(
                    f"{source}: duplicate label '{label}' "
                    f"(already declared in {first_source})"
                )
            else:
                declared_labels[label] = (source, node)

    # Second pass: validate each file fully.
    for config in configs:
        source = config.get("_source", "?")
        app_label = config.get("app", "")

        # App must exist.
        if not app_label:
            errors.append(f"{source}: missing 'app' key")
        else:
            try:
                django_apps.get_app_config(app_label)
            except LookupError:
                errors.append(f"{source}: unknown Django app '{app_label}'")

        # ---- nodes ----
        for node in config.get("nodes", []):
            model_path = node.get("model", "")
            uuid_field = node.get("uuid_field", "uid")

            if not model_path:
                errors.append(f"{source}: node '{node.get('label')}' missing 'model'")
                continue

            try:
                model = import_model(model_path)
            except Exception as exc:
                errors.append(
                    f"{source}: cannot import model '{model_path}': {exc}"
                )
                continue

            if not hasattr(model, uuid_field):
                errors.append(
                    f"{source}: model '{model_path}' has no attribute '{uuid_field}'"
                )

            for neo_field, field_def in node.get("fields", {}).items():
                sql_field = (
                    field_def.get("source", neo_field)
                    if isinstance(field_def, dict)
                    else field_def
                )
                if not hasattr(model, sql_field):
                    errors.append(
                        f"{source}: model '{model_path}' has no attribute '{sql_field}' "
                        f"(mapped to '{neo_field}')"
                    )

        # ---- links ----
        for link in config.get("links", []):
            from_label = link.get("from_label", "")
            to_label = link.get("to_label", "")
            generic = bool(link.get("generic"))

            if from_label not in declared_labels:
                errors.append(
                    f"{source}: link references unknown from_label '{from_label}'"
                )
            # Generic (GenericForeignKey) junction links resolve their target
            # label at runtime, so they declare no fixed to_label.
            if not generic and to_label not in declared_labels:
                errors.append(
                    f"{source}: link references unknown to_label '{to_label}'"
                )

            via_model_path = link.get("via_model")
            if via_model_path:
                # Junction-model link — validate the junction model itself.
                from_field = link.get("from_field", "")
                to_field = link.get("to_field", "")
                try:
                    via_model = import_model(via_model_path)
                    if from_field and not hasattr(via_model, from_field):
                        errors.append(
                            f"{source}: via_model '{via_model_path}' "
                            f"has no field '{from_field}'"
                        )
                    if to_field and not hasattr(via_model, to_field):
                        errors.append(
                            f"{source}: via_model '{via_model_path}' "
                            f"has no field '{to_field}'"
                        )
                except Exception as exc:
                    errors.append(
                        f"{source}: cannot import via_model '{via_model_path}': {exc}"
                    )
            else:
                # FK / M2M link — validate source field on the from-model.
                source_field = link.get("source", "")
                if from_label in declared_labels:
                    _, from_node = declared_labels[from_label]
                    model_path = from_node.get("model", "")
                    if model_path:
                        try:
                            model = import_model(model_path)
                            if source_field and not hasattr(model, source_field):
                                errors.append(
                                    f"{source}: model '{model_path}' "
                                    f"has no attribute '{source_field}'"
                                )
                        except Exception:
                            pass  # already reported above

    return errors
