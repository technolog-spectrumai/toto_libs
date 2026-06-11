from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from .loader import import_model, load_all_configs
from .projection import _get_value, _neo4j_property_value, link_key


META_PROPS = {"ravioli_uuid"}


def _jsonable(value):
    return DjangoJSONEncoder().default(value)


def normalize(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [normalize(v) for v in value]
    try:
        return _jsonable(value)
    except TypeError:
        return str(value)


def clean_props(props):
    return {
        key: normalize(value)
        for key, value in (props or {}).items()
        if key not in META_PROPS
    }


def neo4j_props(props):
    return {
        key: _neo4j_property_value(value)
        for key, value in (props or {}).items()
    }


def expected_node(node_def, obj):
    uuid_field = node_def.get("uuid_field", "uid")
    field_map = node_def.get("fields", {})
    return {
        "label": node_def["label"],
        "uuid": str(getattr(obj, uuid_field)),
        "props": {
            key: normalize(_get_value(obj, field_def))
            for key, field_def in field_map.items()
        },
    }


def _junction_relation_uuid(junction_obj):
    if hasattr(junction_obj, "uid"):
        return str(junction_obj.uid)
    return str(junction_obj.pk)


# Stable diff keys for relationships. Module-level so the NeoJSON loader can build
# `expected` sets that compare against the planner's `actual` sets identically.
def direct_relationship_key(rel):
    return "|".join([
        "direct",
        rel["from_label"],
        rel["from_uuid"],
        rel["relation"],
        rel["to_label"],
        rel["to_uuid"],
    ])


def junction_relationship_key(rel):
    return "|".join([
        "junction",
        rel["relation"],
        rel["ravioli_uuid"],
    ])


class ProjectionPlanner:
    def __init__(self, client, configs=None, selected_labels=None):
        self.client = client
        self.configs = configs if configs is not None else load_all_configs()
        self.selected_labels = set(selected_labels or [])
        self._label_map = self._build_label_map()
        # Reverse map for resolving `generic: true` junction targets (GenericFK).
        self._label_by_model = {
            info["model"]: (label, info["uuid_field"])
            for label, info in self._label_map.items()
        }

    def _build_label_map(self):
        mapping = {}
        for config in self.configs:
            for node in config.get("nodes", []):
                mapping[node["label"]] = {
                    "node_def": node,
                    "model": import_model(node["model"]),
                    "uuid_field": node.get("uuid_field", "uid"),
                }
        return mapping

    def _node_selected(self, node_def):
        return not self.selected_labels or node_def["label"] in self.selected_labels

    def _link_selected(self, link_def):
        return (
            not self.selected_labels
            or link_def["from_label"] in self.selected_labels
        )

    def selected_node_defs(self):
        result = []
        for config in self.configs:
            for node_def in config.get("nodes", []):
                if self._node_selected(node_def):
                    result.append(node_def)
        return result

    def selected_link_defs(self):
        result = []
        for config in self.configs:
            for link_def in config.get("links", []):
                if self._link_selected(link_def):
                    result.append(link_def)
        return result

    def expected_nodes(self):
        expected = {}
        for node_def in self.selected_node_defs():
            model = import_model(node_def["model"])
            label = node_def["label"]
            expected[label] = {}
            for obj in model.objects.all().iterator():
                node = expected_node(node_def, obj)
                expected[label][node["uuid"]] = node
        return expected

    def expected_relationships(self):
        expected = {}
        for link_def in self.selected_link_defs():
            if link_def.get("via_model"):
                expected.update(self._expected_junction_relationships(link_def))
            else:
                expected.update(self._expected_fk_relationships(link_def))
        return expected

    def _direct_relationship_key(self, rel):
        return direct_relationship_key(rel)

    def _junction_relationship_key(self, rel):
        return junction_relationship_key(rel)

    def _expected_fk_relationships(self, link_def):
        from_info = self._label_map[link_def["from_label"]]
        to_info = self._label_map[link_def["to_label"]]
        from_model = from_info["model"]
        to_model = to_info["model"]
        from_uuid_field = from_info["uuid_field"]
        to_uuid_field = to_info["uuid_field"]
        source_field = link_def["source"]
        cardinality = link_def.get("cardinality", "one")
        result = {}

        for obj in from_model.objects.all().iterator():
            from_uuid = str(getattr(obj, from_uuid_field))
            related_objects = []

            if cardinality == "many":
                related_objects = list(getattr(obj, source_field).all())
            else:
                id_attr = f"{source_field}_id"
                has_value = (
                    bool(getattr(obj, id_attr))
                    if hasattr(obj, id_attr)
                    else bool(getattr(obj, source_field, None))
                )
                if has_value:
                    related = getattr(obj, source_field, None)
                    if related is not None:
                        related_objects = [related]

            for related in related_objects:
                # The FK target must actually be an instance of the declared
                # to_label model. A cross-model link (e.g. an `assignee` FK to
                # Practitioner declared as `-> Person`) reads a uuid that no
                # Person node has, so the MERGE matches nothing and the diff
                # never converges — skip it rather than propose a dead edge.
                if not isinstance(related, to_model):
                    continue
                rel = {
                    "kind": "direct",
                    "from_label": link_def["from_label"],
                    "from_uuid": from_uuid,
                    "relation": link_def["relation"],
                    "to_label": link_def["to_label"],
                    "to_uuid": str(getattr(related, to_uuid_field)),
                    "props": {},
                    "link_key": link_key(link_def),
                }
                rel["key"] = self._direct_relationship_key(rel)
                result[rel["key"]] = rel

        return result

    def _expected_junction_relationships(self, link_def):
        via_model = import_model(link_def["via_model"])
        from_info = self._label_map[link_def["from_label"]]
        from_uuid_field = from_info["uuid_field"]
        from_field = link_def["from_field"]
        to_field = link_def["to_field"]
        generic = bool(link_def.get("generic"))
        props_map = link_def.get("props", {})
        result = {}

        # A GenericForeignKey target can't be select_related.
        related = [from_field] if generic else [from_field, to_field]
        qs = via_model.objects.select_related(*related).all()
        for junction_obj in qs.iterator():
            from_obj = getattr(junction_obj, from_field, None)
            to_obj = getattr(junction_obj, to_field, None)
            if from_obj is None or to_obj is None:
                continue

            if generic:
                target = self._label_by_model.get(type(to_obj))
                if target is None:
                    continue
                to_label, to_uuid_field = target
            else:
                to_label = link_def["to_label"]
                to_uuid_field = self._label_map[to_label]["uuid_field"]

            rel = {
                "kind": "junction",
                "from_label": link_def["from_label"],
                "from_uuid": str(getattr(from_obj, from_uuid_field)),
                "relation": link_def["relation"],
                "to_label": to_label,
                "to_uuid": str(getattr(to_obj, to_uuid_field)),
                "ravioli_uuid": _junction_relation_uuid(junction_obj),
                "props": {
                    rel_field: normalize(_get_value(junction_obj, sql_field))
                    for rel_field, sql_field in props_map.items()
                },
                "link_key": link_key(link_def),
            }
            rel["key"] = self._junction_relationship_key(rel)
            result[rel["key"]] = rel

        return result

    def actual_nodes(self):
        actual = {}
        for node_def in self.selected_node_defs():
            label = node_def["label"]
            records = self.client.run_cypher(
                (
                    f"MATCH (n:{label}) "
                    "WHERE n.uuid IS NOT NULL "
                    # Skip :HISTORICAL version snapshots written by ravioli's
                    # per-object export — they are not canonical SQL rows and
                    # must not be diffed/deleted by a full sync.
                    "AND coalesce(n._historical, false) = false "
                    "RETURN n.uuid AS uuid, properties(n) AS props"
                )
            )
            actual[label] = {}
            for record in records:
                uuid = str(record["uuid"])
                actual[label][uuid] = {
                    "label": label,
                    "uuid": uuid,
                    "props": clean_props(record["props"]),
                }
        return actual

    def actual_relationships(self):
        actual = {}
        for link_def in self.selected_link_defs():
            if link_def.get("via_model"):
                actual.update(self._actual_junction_relationships(link_def))
            else:
                actual.update(self._actual_direct_relationships(link_def))
        return actual

    def _actual_direct_relationships(self, link_def):
        from_label = link_def["from_label"]
        to_label = link_def["to_label"]
        relation = link_def["relation"]
        records = self.client.run_cypher(
            (
                f"MATCH (a:{from_label})-[r:{relation}]->(b:{to_label}) "
                "WHERE a.uuid IS NOT NULL AND b.uuid IS NOT NULL "
                "RETURN a.uuid AS from_uuid, b.uuid AS to_uuid, "
                "properties(r) AS props"
            )
        )
        result = {}
        for record in records:
            rel = {
                "kind": "direct",
                "from_label": from_label,
                "from_uuid": str(record["from_uuid"]),
                "relation": relation,
                "to_label": to_label,
                "to_uuid": str(record["to_uuid"]),
                "props": clean_props(record["props"]),
                "link_key": link_key(link_def),
            }
            rel["key"] = self._direct_relationship_key(rel)
            result[rel["key"]] = rel
        return result

    def _actual_junction_relationships(self, link_def):
        from_label = link_def["from_label"]
        relation = link_def["relation"]
        generic = bool(link_def.get("generic"))

        if generic:
            # Target label varies — match any node and capture its labels.
            query = (
                f"MATCH (a:{from_label})-[r:{relation}]->(b) "
                "WHERE r.ravioli_uuid IS NOT NULL "
                "AND a.uuid IS NOT NULL AND b.uuid IS NOT NULL "
                "RETURN a.uuid AS from_uuid, b.uuid AS to_uuid, labels(b) AS to_labels, "
                "r.ravioli_uuid AS ravioli_uuid, properties(r) AS props"
            )
        else:
            to_label = link_def["to_label"]
            query = (
                f"MATCH (a:{from_label})-[r:{relation}]->(b:{to_label}) "
                "WHERE r.ravioli_uuid IS NOT NULL "
                "AND a.uuid IS NOT NULL AND b.uuid IS NOT NULL "
                "RETURN a.uuid AS from_uuid, b.uuid AS to_uuid, "
                "r.ravioli_uuid AS ravioli_uuid, properties(r) AS props"
            )
        records = self.client.run_cypher(query)
        result = {}
        for record in records:
            if generic:
                labels = [lbl for lbl in (record.get("to_labels") or []) if lbl in self._label_map]
                row_to_label = labels[0] if labels else ""
            else:
                row_to_label = link_def["to_label"]
            rel = {
                "kind": "junction",
                "from_label": from_label,
                "from_uuid": str(record["from_uuid"]),
                "relation": relation,
                "to_label": row_to_label,
                "to_uuid": str(record["to_uuid"]),
                "ravioli_uuid": str(record["ravioli_uuid"]),
                "props": clean_props(record["props"]),
                "link_key": link_key(link_def),
            }
            rel["key"] = self._junction_relationship_key(rel)
            result[rel["key"]] = rel
        return result

    def build_diff(self):
        return compute_diff(
            self.expected_nodes(),
            self.actual_nodes(),
            self.expected_relationships(),
            self.actual_relationships(),
        )


def compute_diff(expected_nodes, actual_nodes, expected_relationships, actual_relationships):
    """Compare desired (`expected`) vs current (`actual`) node/relationship sets and
    return ``(diff, totals)``. This is the shared sync algorithm: the SQL projection
    planner and the NeoJSON loader both feed it ``expected``/``actual`` in the same
    dict shape (nodes keyed ``{label: {uuid: node}}``; relationships keyed by their
    stable diff key)."""
    diff = {
        "nodes": {"create": [], "update": [], "delete": [], "ignored": []},
        "relationships": {
            "create": [],
            "update": [],
            "delete": [],
            "ignored": [],
        },
    }

    for label, nodes in expected_nodes.items():
        graph_nodes = actual_nodes.get(label, {})
        for uuid, expected in nodes.items():
            actual = graph_nodes.get(uuid)
            if actual is None:
                diff["nodes"]["create"].append(expected)
                continue

            changes = prop_changes(expected["props"], actual["props"])
            if changes:
                diff["nodes"]["update"].append({**expected, "changes": changes})

        for uuid, actual in graph_nodes.items():
            if uuid not in nodes:
                diff["nodes"]["delete"].append(actual)

    for key, expected in expected_relationships.items():
        actual = actual_relationships.get(key)
        if actual is None:
            diff["relationships"]["create"].append(expected)
            continue

        changes = prop_changes(expected["props"], actual["props"])
        endpoint_changed = (
            expected["from_uuid"] != actual["from_uuid"]
            or expected["to_uuid"] != actual["to_uuid"]
        )
        if changes or endpoint_changed:
            diff["relationships"]["update"].append({
                **expected,
                "changes": changes,
                "endpoint_changed": endpoint_changed,
            })

    for key, actual in actual_relationships.items():
        if key not in expected_relationships:
            diff["relationships"]["delete"].append(actual)

    totals = {
        "expected_nodes": sum(len(nodes) for nodes in expected_nodes.values()),
        "actual_nodes": sum(len(nodes) for nodes in actual_nodes.values()),
        "expected_relationships": len(expected_relationships),
        "actual_relationships": len(actual_relationships),
    }
    return diff, totals


def prop_changes(expected_props, actual_props):
    changes = {}
    for key, expected_value in expected_props.items():
        actual_value = actual_props.get(key)
        if normalize(actual_value) != normalize(expected_value):
            changes[key] = {
                "from": normalize(actual_value),
                "to": normalize(expected_value),
            }
    return changes


def summarize_diff(diff, totals=None):
    summary = {
        "nodes": {},
        "relationships": {},
        "total_changes": 0,
        "totals": totals or {},
    }
    for group in ("nodes", "relationships"):
        for action in ("create", "update", "delete", "ignored"):
            count = len(diff[group].get(action, []))
            summary[group][action] = count
            if action != "ignored":
                summary["total_changes"] += count
    return summary


def create_projection_plan(client, labels=None, configs=None):
    from .models import GraphProjectionPlan

    planner = ProjectionPlanner(client, configs=configs, selected_labels=labels)
    diff, totals = planner.build_diff()
    summary = summarize_diff(diff, totals)
    return GraphProjectionPlan.objects.create(
        status=GraphProjectionPlan.STATUS_READY,
        scope={"labels": labels or []},
        summary=summary,
        diff=diff,
    )


def apply_projection_plan(client, plan):
    applier = ProjectionPlanApplier(client)
    applier.apply(plan.diff)
    plan.status = plan.STATUS_APPLIED
    plan.applied_at = timezone.now()
    plan.error = ""
    plan.save(update_fields=["status", "applied_at", "error", "updated_at"])


class ProjectionPlanApplier:
    def __init__(self, client):
        self.client = client

    def apply(self, diff):
        for node in diff["nodes"].get("create", []):
            self.upsert_node(node)
        for node in diff["nodes"].get("update", []):
            self.upsert_node(node)
        for rel in diff["relationships"].get("create", []):
            self.upsert_relationship(rel)
        for rel in diff["relationships"].get("update", []):
            self.upsert_relationship(rel)
        for rel in diff["relationships"].get("delete", []):
            self.delete_relationship(rel)
        for node in diff["nodes"].get("delete", []):
            self.delete_node(node)

    def upsert_node(self, node):
        label = node["label"]
        self.client.run_cypher(
            f"MERGE (n:{label} {{uuid: $uuid}}) SET n += $props",
            {"uuid": node["uuid"], "props": neo4j_props(node.get("props", {}))},
        )

    def delete_node(self, node):
        label = node["label"]
        self.client.run_cypher(
            f"MATCH (n:{label} {{uuid: $uuid}}) DETACH DELETE n",
            {"uuid": node["uuid"]},
        )

    def upsert_relationship(self, rel):
        if rel["kind"] == "junction":
            self.upsert_junction_relationship(rel)
        else:
            self.upsert_direct_relationship(rel)

    def upsert_direct_relationship(self, rel):
        self.client.run_cypher(
            (
                f"MATCH (a:{rel['from_label']} {{uuid: $from_uuid}}) "
                f"MATCH (b:{rel['to_label']} {{uuid: $to_uuid}}) "
                f"MERGE (a)-[r:{rel['relation']}]->(b) "
                "SET r += $props"
            ),
            {
                "from_uuid": rel["from_uuid"],
                "to_uuid": rel["to_uuid"],
                "props": neo4j_props(rel.get("props", {})),
            },
        )

    def upsert_junction_relationship(self, rel):
        self.client.run_cypher(
            f"MATCH ()-[r:{rel['relation']} {{ravioli_uuid: $rel_uuid}}]->() DELETE r",
            {"rel_uuid": rel["ravioli_uuid"]},
        )
        self.client.run_cypher(
            (
                f"MATCH (a:{rel['from_label']} {{uuid: $from_uuid}}) "
                f"MATCH (b:{rel['to_label']} {{uuid: $to_uuid}}) "
                f"CREATE (a)-[r:{rel['relation']} {{ravioli_uuid: $rel_uuid}}]->(b) "
                "SET r += $props"
            ),
            {
                "from_uuid": rel["from_uuid"],
                "to_uuid": rel["to_uuid"],
                "rel_uuid": rel["ravioli_uuid"],
                "props": neo4j_props(rel.get("props", {})),
            },
        )

    def delete_relationship(self, rel):
        if rel["kind"] == "junction":
            self.client.run_cypher(
                f"MATCH ()-[r:{rel['relation']} {{ravioli_uuid: $rel_uuid}}]->() DELETE r",
                {"rel_uuid": rel["ravioli_uuid"]},
            )
            return

        self.client.run_cypher(
            (
                f"MATCH (a:{rel['from_label']} {{uuid: $from_uuid}})"
                f"-[r:{rel['relation']}]->"
                f"(b:{rel['to_label']} {{uuid: $to_uuid}}) DELETE r"
            ),
            {"from_uuid": rel["from_uuid"], "to_uuid": rel["to_uuid"]},
        )
