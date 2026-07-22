"""
ravioli.projection — YAML-driven SQL → Neo4j projection.

No neomodel.  All graph writes go through raw Cypher via ravioli.connection.
Django apps supply the source data; ravioli/graph/*.yaml owns the graph shape;
this module owns the projection logic.
"""

import json

from django.core.serializers.json import DjangoJSONEncoder

from .loader import load_all_configs, import_model


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------

PRIMITIVE_PROPERTY_TYPES = (str, int, float, bool)


def _json_property(value):
    return json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True)


def _neo4j_property_value(value):
    if value is None or isinstance(value, PRIMITIVE_PROPERTY_TYPES):
        return value
    if isinstance(value, (list, tuple, set)):
        normalized = [_neo4j_property_value(item) for item in value]
        if all(
            item is None or isinstance(item, PRIMITIVE_PROPERTY_TYPES)
            for item in normalized
        ):
            return normalized
        return _json_property(list(value))
    if isinstance(value, dict):
        return _json_property(value)
    return value

def _apply_transform(value, transform):
    if transform == "wkt":
        return value.wkt if value else None
    if transform == "str":
        return str(value) if value is not None else None
    if transform == "json":
        return _json_property(value or {})
    if transform == "file_url":
        try:
            return value.url if value else None
        except (ValueError, AttributeError):
            return None
    if transform == "default_dict":
        return _json_property(value or {})
    return _neo4j_property_value(value)


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


def link_key(link_def):
    """Return a stable key for a YAML link definition."""
    source = link_def.get("source") or link_def.get("via_model") or ""
    return "|".join([
        link_def.get("from_label", ""),
        link_def.get("to_label", ""),
        link_def.get("relation", ""),
        source,
    ])


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
        # Reverse map (model class → label, uuid_field) for resolving the target
        # of a `generic: true` junction link (a GenericForeignKey).
        self._label_by_model = {
            info["model"]: (label, info["uuid_field"])
            for label, info in self._label_map.items()
        }
        self._node_defs_by_label = self._build_node_def_map()
        self._links_by_from_label = self._build_links_by_from_label()
        self._links_by_key = self._build_links_by_key()

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

    def _build_node_def_map(self):
        mapping = {}
        for config in self.configs:
            for node in config.get("nodes", []):
                mapping[node["label"]] = node
        return mapping

    def _build_links_by_from_label(self):
        mapping = {}
        for config in self.configs:
            for link in config.get("links", []):
                mapping.setdefault(link["from_label"], []).append(link)
        return mapping

    def _build_links_by_key(self):
        mapping = {}
        for config in self.configs:
            for link in config.get("links", []):
                mapping[link_key(link)] = link
        return mapping

    def node_defs(self):
        return list(self._node_defs_by_label.values())

    def link_defs(self):
        return list(self._links_by_key.values())

    def get_link_def(self, key):
        return self._links_by_key[key]

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
        for obj in model.objects.all().iterator():
            self._project_node_instance(node_def, obj)

    def _project_node_instance(self, node_def, obj):
        label = node_def["label"]
        uuid_field = node_def.get("uuid_field", "uid")
        field_map = node_def.get("fields", {})
        uuid = str(getattr(obj, uuid_field))
        props = {k: _get_value(obj, v) for k, v in field_map.items()}

        self.client.run_cypher(
            f"MERGE (n:{label} {{uuid: $uuid}}) SET n += $props",
            {"uuid": uuid, "props": props},
        )

    def project_node_by_label(self, label, uuid):
        node_def = self._node_defs_by_label[label]
        info = self._label_map[label]
        obj = info["model"].objects.get(**{info["uuid_field"]: uuid})
        self._project_node_instance(node_def, obj)
        return obj

    def delete_node_by_label(self, label, uuid):
        self.client.run_cypher(
            f"MATCH (n:{label} {{uuid: $uuid}}) DETACH DELETE n",
            {"uuid": str(uuid)},
        )

    def project_outgoing_links_by_label(self, label, uuid):
        info = self._label_map[label]
        obj = info["model"].objects.get(**{info["uuid_field"]: uuid})
        for link_def in self._links_by_from_label.get(label, []):
            if not link_def.get("via_model"):
                self._project_fk_link_instance(link_def, obj)
        return obj

    def project_link_source_by_key(self, key, uuid):
        link_def = self._links_by_key[key]
        if link_def.get("via_model"):
            return
        from_info = self._label_map[link_def["from_label"]]
        obj = from_info["model"].objects.get(**{from_info["uuid_field"]: uuid})
        self._project_fk_link_instance(link_def, obj)

    def project_link_by_key(self, key):
        self._project_link(self._links_by_key[key])

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
        from_info = self._label_map[link_def["from_label"]]
        for obj in from_info["model"].objects.all().iterator():
            self._project_fk_link_instance(link_def, obj)

    def _project_fk_link_instance(self, link_def, obj):
        from_label = link_def["from_label"]
        to_label = link_def["to_label"]
        relation = link_def["relation"]
        source_field = link_def["source"]
        cardinality = link_def.get("cardinality", "one")
        nullable = link_def.get("nullable", True)

        from_info = self._label_map[from_label]
        to_info = self._label_map[to_label]
        from_uuid_field = from_info["uuid_field"]
        to_uuid_field = to_info["uuid_field"]

        merge_query = (
            f"MATCH (a:{from_label} {{uuid: $f}}) "
            f"MATCH (b:{to_label} {{uuid: $t}}) "
            f"MERGE (a)-[r:{relation}]->(b)"
        )
        clear_query = (
            f"MATCH (a:{from_label} {{uuid: $uuid}})"
            f"-[r:{relation}]->() DELETE r"
        )

        from_uuid = str(getattr(obj, from_uuid_field))

        # Clear stale relationships before re-syncing this source object.
        self.client.run_cypher(clear_query, {"uuid": from_uuid})

        params_base = {"f": from_uuid}

        if cardinality == "many":
            for related in getattr(obj, source_field).all():
                to_uuid = str(getattr(related, to_uuid_field))
                self.client.run_cypher(merge_query, {**params_base, "t": to_uuid})
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
                    self.client.run_cypher(merge_query, {**params_base, "t": to_uuid})

    # ------------------------------------------------------------------
    # Junction-model link  (separate SQL model carries relationship props)
    # ------------------------------------------------------------------

    def _project_junction_link(self, link_def):
        relation = link_def["relation"]
        via_model_path = link_def["via_model"]
        from_field = link_def["from_field"]
        to_field = link_def["to_field"]
        generic = bool(link_def.get("generic"))

        via_model = import_model(via_model_path)

        # Wipe all relationships of this type globally before re-creating.
        self.client.run_cypher(f"MATCH ()-[r:{relation}]->() DELETE r")

        # A GenericForeignKey target can't be select_related — for generic links
        # only the concrete `from` side is prefetched; the target is resolved
        # per-row in the instance projector.
        related = [from_field] if generic else [from_field, to_field]
        qs = via_model.objects.select_related(*related).all()
        for junction_obj in qs.iterator():
            self._project_junction_link_instance(link_def, junction_obj)

    def _junction_relation_uuid(self, junction_obj):
        if hasattr(junction_obj, "uid"):
            return str(junction_obj.uid)
        return str(junction_obj.pk)

    def _project_junction_link_instance(self, link_def, junction_obj):
        from_label = link_def["from_label"]
        relation = link_def["relation"]
        from_field = link_def["from_field"]
        to_field = link_def["to_field"]
        generic = bool(link_def.get("generic"))
        props_map = link_def.get("props", {})

        from_info = self._label_map[from_label]
        from_uuid_field = from_info["uuid_field"]

        from_obj = getattr(junction_obj, from_field, None)
        to_obj = getattr(junction_obj, to_field, None)
        if from_obj is None or to_obj is None:
            return

        if generic:
            # Resolve the target's graph label from its model class.
            target = self._label_by_model.get(type(to_obj))
            if target is None:
                return  # target model isn't projected to the graph
            to_label, to_uuid_field = target
        else:
            to_label = link_def["to_label"]
            to_uuid_field = self._label_map[to_label]["uuid_field"]

        rel_uuid = self._junction_relation_uuid(junction_obj)
        props = {
            rel_field: _get_value(junction_obj, sql_field)
            for rel_field, sql_field in props_map.items()
        }

        # If the SQL junction row changed endpoints, remove the old graph
        # relationship carrying the same projection identity before re-creating.
        self.delete_junction_relation(link_def, rel_uuid)

        self.client.run_cypher(
            (
                f"MATCH (a:{from_label} {{uuid: $f}}) "
                f"MATCH (b:{to_label} {{uuid: $t}}) "
                f"CREATE (a)-[r:{relation} {{ravioli_uuid: $rel_uuid}}]->(b) "
                "SET r += $props"
            ),
            {
                "f": str(getattr(from_obj, from_uuid_field)),
                "t": str(getattr(to_obj, to_uuid_field)),
                "rel_uuid": rel_uuid,
                "props": props,
            },
        )

    def project_junction_by_key(self, key, uuid):
        link_def = self._links_by_key[key]
        via_model = import_model(link_def["via_model"])
        lookup = {"uid": uuid} if hasattr(via_model, "uid") else {"pk": uuid}
        junction_obj = via_model.objects.get(**lookup)
        self._project_junction_link_instance(link_def, junction_obj)

    def delete_junction_relation(self, link_def, rel_uuid):
        relation = link_def["relation"]
        self.client.run_cypher(
            f"MATCH ()-[r:{relation} {{ravioli_uuid: $rel_uuid}}]->() DELETE r",
            {"rel_uuid": str(rel_uuid)},
        )
