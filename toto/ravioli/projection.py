"""
ravioli.projection — YAML-driven SQL → Neo4j projection.

No neomodel.  All graph writes go through raw Cypher via ravioli.connection.
Django apps supply the source data; ravioli/graph/*.yaml owns the graph shape;
this module owns the projection logic.
"""

import json
import importlib

from .loader import load_all_configs, import_model


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------

def _apply_transform(value, transform):
    if transform == "wkt":
        return value.wkt if value else None
    if transform == "str":
        return str(value) if value is not None else None
    if transform == "json":
        return json.dumps(value or {})
    if transform == "file_url":
        try:
            return value.url if value else None
        except (ValueError, AttributeError):
            return None
    if transform == "default_dict":
        return value or {}
    return value


def _get_value(obj, field_def):
    """Read one field from a Django model instance, applying optional transforms."""
    if isinstance(field_def, dict):
        sql_field = field_def.get("source")
        transform = field_def.get("transform")
    else:
        sql_field = field_def
        transform = None

    value = getattr(obj, sql_field, None)
    return _apply_transform(value, transform)


# ---------------------------------------------------------------------------
# Projection runner
# ---------------------------------------------------------------------------

class ProjectionRunner:
    """
    Projects all nodes first, then all links.

    client  — a ravioli.connection.Neo4jClient instance
    configs — output of ravioli.loader.load_all_configs()
              (pass explicitly so the runner can be constructed without I/O)
    """

    def __init__(self, client, configs=None):
        self.client = client
        self.configs = configs if configs is not None else load_all_configs()
        self._label_map = self._build_label_map()

    # ------------------------------------------------------------------
    # Label → model mapping (built once, used for every link lookup)
    # ------------------------------------------------------------------

    def _build_label_map(self):
        mapping = {}
        for config in self.configs:
            for node in config.get("nodes", []):
                label = node["label"]
                mapping[label] = {
                    "node_def": node,
                    "model": import_model(node["model"]),
                    "uuid_field": node.get("uuid_field", "uid"),
                }
        return mapping

    # ------------------------------------------------------------------
    # Grouped labels — used by admin / views for display
    # ------------------------------------------------------------------

    def grouped_models(self):
        result = {}
        for config in self.configs:
            app = config.get("app", "")
            result[app] = [node["label"] for node in config.get("nodes", [])]
        return result

    # ------------------------------------------------------------------
    # Two-pass public API
    # ------------------------------------------------------------------

    def project_all_nodes(self):
        for config in self.configs:
            for node_def in config.get("nodes", []):
                self._project_node(node_def)

    def project_all_links(self):
        for config in self.configs:
            for link_def in config.get("links", []):
                self._project_link(link_def)

    def run(self):
        self.project_all_nodes()
        self.project_all_links()

    # ------------------------------------------------------------------
    # Streaming progress (for the admin SSE view)
    # ------------------------------------------------------------------

    def run_with_progress(self, selected_labels=None):
        node_defs = []
        link_defs = []
        for config in self.configs:
            for node in config.get("nodes", []):
                if selected_labels is None or node["label"] in selected_labels:
                    node_defs.append(node)
            for link in config.get("links", []):
                if selected_labels is None or link.get("from_label") in selected_labels:
                    link_defs.append(link)

        total = len(node_defs) + len(link_defs)
        current = 0

        yield {
            "status": "started",
            "current": 0,
            "total": total,
            "message": "Starting projection",
        }

        for node_def in node_defs:
            self._project_node(node_def)
            current += 1
            yield {
                "status": "running",
                "phase": "nodes",
                "label": node_def["label"],
                "model": node_def.get("model", ""),
                "current": current,
                "total": total,
                "message": f"Projected nodes: {node_def['label']}",
            }

        for link_def in link_defs:
            self._project_link(link_def)
            current += 1
            yield {
                "status": "running",
                "phase": "links",
                "relation": link_def.get("relation", ""),
                "current": current,
                "total": total,
                "message": f"Projected links: {link_def.get('relation', '')}",
            }

        yield {
            "status": "completed",
            "current": total,
            "total": total,
            "message": "Projection complete",
        }

    # ------------------------------------------------------------------
    # Node projection
    # ------------------------------------------------------------------

    def _project_node(self, node_def):
        model = import_model(node_def["model"])
        label = node_def["label"]
        uuid_field = node_def.get("uuid_field", "uid")
        field_map = node_def.get("fields", {})

        if not field_map:
            for obj in model.objects.all():
                uuid = str(getattr(obj, uuid_field))
                self.client.run_cypher(
                    f"MERGE (n:{label} {{uuid: $uuid}})",
                    {"uuid": uuid},
                )
            return

        set_clause = ", ".join(f"n.{k} = ${k}" for k in field_map)
        query = f"MERGE (n:{label} {{uuid: $uuid}}) SET {set_clause}"

        for obj in model.objects.all():
            uuid = str(getattr(obj, uuid_field))
            props = {k: _get_value(obj, v) for k, v in field_map.items()}
            self.client.run_cypher(query, {"uuid": uuid, **props})

    # ------------------------------------------------------------------
    # Link projection (dispatch)
    # ------------------------------------------------------------------

    def _project_link(self, link_def):
        if link_def.get("via_model"):
            self._project_junction_link(link_def)
        else:
            self._project_fk_link(link_def)

    # ------------------------------------------------------------------
    # FK / M2M link
    # ------------------------------------------------------------------

    def _project_fk_link(self, link_def):
        from_label = link_def["from_label"]
        to_label = link_def["to_label"]
        relation = link_def["relation"]
        source_field = link_def["source"]
        cardinality = link_def.get("cardinality", "one")
        nullable = link_def.get("nullable", True)

        from_info = self._label_map[from_label]
        to_info = self._label_map[to_label]
        from_model = from_info["model"]
        from_uuid_field = from_info["uuid_field"]
        to_uuid_field = to_info["uuid_field"]

        merge_query = (
            f"MATCH (a:{from_label} {{uuid: $f}}) "
            f"MATCH (b:{to_label} {{uuid: $t}}) "
            f"MERGE (a)-[:{relation}]->(b)"
        )
        clear_query = (
            f"MATCH (a:{from_label} {{uuid: $uuid}})"
            f"-[r:{relation}]->() DELETE r"
        )

        for obj in from_model.objects.all():
            from_uuid = str(getattr(obj, from_uuid_field))

            # Clear stale relationships before re-syncing.
            self.client.run_cypher(clear_query, {"uuid": from_uuid})

            if cardinality == "many":
                for related in getattr(obj, source_field).all():
                    to_uuid = str(getattr(related, to_uuid_field))
                    self.client.run_cypher(merge_query, {"f": from_uuid, "t": to_uuid})
            else:
                # Use the _id shortcut to avoid an extra DB hit when the FK is null.
                id_attr = f"{source_field}_id"
                has_value = (
                    bool(getattr(obj, id_attr))
                    if hasattr(obj, id_attr)
                    else bool(getattr(obj, source_field, None))
                )
                if has_value:
                    related = getattr(obj, source_field, None)
                    if related is not None:
                        to_uuid = str(getattr(related, to_uuid_field))
                        self.client.run_cypher(merge_query, {"f": from_uuid, "t": to_uuid})

    # ------------------------------------------------------------------
    # Junction-model link  (separate SQL model carries relationship props)
    # ------------------------------------------------------------------

    def _project_junction_link(self, link_def):
        from_label = link_def["from_label"]
        to_label = link_def["to_label"]
        relation = link_def["relation"]
        via_model_path = link_def["via_model"]
        from_field = link_def["from_field"]
        to_field = link_def["to_field"]
        props_map = link_def.get("props", {})

        via_model = import_model(via_model_path)
        from_info = self._label_map[from_label]
        to_info = self._label_map[to_label]
        from_uuid_field = from_info["uuid_field"]
        to_uuid_field = to_info["uuid_field"]

        # Wipe all relationships of this type globally before re-creating.
        self.client.run_cypher(f"MATCH ()-[r:{relation}]->() DELETE r")

        if props_map:
            set_clause = ", ".join(f"r.{k} = ${k}" for k in props_map)
            query = (
                f"MATCH (a:{from_label} {{uuid: $f}}) "
                f"MATCH (b:{to_label} {{uuid: $t}}) "
                f"CREATE (a)-[r:{relation}]->(b) SET {set_clause}"
            )
        else:
            query = (
                f"MATCH (a:{from_label} {{uuid: $f}}) "
                f"MATCH (b:{to_label} {{uuid: $t}}) "
                f"CREATE (a)-[:{relation}]->(b)"
            )

        qs = via_model.objects.select_related(from_field, to_field).all()
        for junction_obj in qs:
            from_obj = getattr(junction_obj, from_field, None)
            to_obj = getattr(junction_obj, to_field, None)
            if from_obj is None or to_obj is None:
                continue

            from_uuid = str(getattr(from_obj, from_uuid_field))
            to_uuid = str(getattr(to_obj, to_uuid_field))
            params: dict = {"f": from_uuid, "t": to_uuid}

            for rel_field, sql_field in props_map.items():
                params[rel_field] = _get_value(junction_obj, sql_field)

            self.client.run_cypher(query, params)
