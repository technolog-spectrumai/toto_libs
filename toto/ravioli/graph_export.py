"""
ravioli.graph_export — per-object "Export to graph", owned by the Neo4j boundary.

The SQL→graph *shape* still comes from ``toto.sql_neo4j_sync`` (the YAML configs),
but the read-diff-write sync lives here:

  1. compute the desired **1-hop slice** of an object as a graph (node + the
     neighbours its outgoing FK/M2M links point to + those edges), from SQL;
  2. read the matching slice currently in Neo4j;
  3. diff them — per-node status is decided by a content **checksum**
     (unchanged → skip; changed → update + snapshot history);
  4. apply without ever destroying prior state: when a node's checksum changes,
     its previous version is copied into a ``:HISTORICAL`` child node (fresh
     uuid, old uuid kept in ``prev_uuid``), capped at ``RAVIOLI_MAX_HISTORY``.

Workflows, file services and vault files are never exported
(``RAVIOLI_EXPORT_EXCLUDED_APPS``).
"""

import hashlib
import json
import time
import uuid as uuid_lib

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder

# Reuse the YAML mapping + value helpers from the projection layer.
from toto.sql_neo4j_sync.loader import import_model, load_all_configs
from toto.sql_neo4j_sync.planner import neo4j_props, normalize
from toto.sql_neo4j_sync.projection import _get_value


HISTORICAL_REL = "HISTORICAL"

# Properties ravioli manages itself — excluded from the content checksum so that
# bookkeeping never looks like a content change.
RESERVED_PROPS = {"uuid", "_checksum", "_historical", "prev_uuid", "archived_at"}

DEFAULT_EXCLUDED_APPS = ["workflows", "fileservices", "vault"]


# ---------------------------------------------------------------------------
# Settings-backed knobs
# ---------------------------------------------------------------------------

def excluded_apps():
    return set(getattr(settings, "RAVIOLI_EXPORT_EXCLUDED_APPS", DEFAULT_EXCLUDED_APPS))


def is_app_excluded(app_label):
    return app_label in excluded_apps()


def max_history():
    try:
        return max(0, int(getattr(settings, "RAVIOLI_MAX_HISTORY", 3)))
    except (TypeError, ValueError):
        return 3


# ---------------------------------------------------------------------------
# Content checksum
# ---------------------------------------------------------------------------

def content_checksum(props):
    """Stable SHA-256 of a node's mapped (content) properties."""
    payload = json.dumps(
        {k: normalize(v) for k, v in (props or {}).items() if k not in RESERVED_PROPS},
        sort_keys=True,
        cls=DjangoJSONEncoder,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------

class GraphExporter:
    """Computes and applies a single object's 1-hop slice.

    ``client`` — a ``ravioli.connection.Neo4jClient`` (or a compatible fake).
    ``configs`` — output of ``sql_neo4j_sync.loader.load_all_configs``.
    """

    def __init__(self, client, configs=None):
        self.client = client
        self.configs = configs if configs is not None else load_all_configs()
        self._node_def_by_label = {}
        self._model_to_label = {}          # model class -> (label, uuid_field)
        self._links_by_from_label = {}
        for config in self.configs:
            app = config.get("app", "")
            if is_app_excluded(app):
                continue                    # workflows / services / files: never mapped
            for node in config.get("nodes", []):
                label = node["label"]
                self._node_def_by_label[label] = node
                self._model_to_label[import_model(node["model"])] = (
                    label,
                    node.get("uuid_field", "uid"),
                )
            for link in config.get("links", []):
                self._links_by_from_label.setdefault(link["from_label"], []).append(link)

    # -- registry -------------------------------------------------------

    def label_for_model(self, model):
        """(label, uuid_field) for a model class, or None if not graph-mapped."""
        return self._model_to_label.get(model)

    # -- desired slice (from SQL) --------------------------------------

    def _node_entry(self, label, obj):
        node_def = self._node_def_by_label[label]
        uuid_field = node_def.get("uuid_field", "uid")
        props = {
            k: normalize(_get_value(obj, v))
            for k, v in node_def.get("fields", {}).items()
        }
        return {
            "label": label,
            "uuid": str(getattr(obj, uuid_field)),
            "props": props,
            "checksum": content_checksum(props),
        }

    def desired_slice(self, label, obj):
        """Return ``(root, nodes, edges)`` for the object + its 1-hop neighbours.

        ``nodes`` keyed by ``(label, uuid)``; ``edges`` keyed by a stable tuple.
        Junction (``via_model``) links are global, not part of a 1-hop object
        export, and are skipped.
        """
        root = self._node_entry(label, obj)
        nodes = {(label, root["uuid"]): root}
        edges = {}

        for link_def in self._links_by_from_label.get(label, []):
            if link_def.get("via_model"):
                continue
            to_label = link_def["to_label"]
            if to_label not in self._node_def_by_label:
                continue
            source_field = link_def["source"]
            relation = link_def["relation"]

            if link_def.get("cardinality", "one") == "many":
                related_objs = list(getattr(obj, source_field).all())
            else:
                id_attr = f"{source_field}_id"
                has_value = (
                    bool(getattr(obj, id_attr))
                    if hasattr(obj, id_attr)
                    else bool(getattr(obj, source_field, None))
                )
                related_objs = []
                if has_value:
                    related = getattr(obj, source_field, None)
                    if related is not None:
                        related_objs = [related]

            for related in related_objs:
                entry = self._node_entry(to_label, related)
                nodes[(to_label, entry["uuid"])] = entry
                edges[(label, root["uuid"], relation, to_label, entry["uuid"])] = {
                    "from_label": label,
                    "from_uuid": root["uuid"],
                    "relation": relation,
                    "to_label": to_label,
                    "to_uuid": entry["uuid"],
                }
        return root, nodes, edges

    # -- actual slice (from Neo4j) -------------------------------------

    def read_node(self, label, uuid):
        records = self.client.run_cypher(
            f"MATCH (n:{label} {{uuid: $uuid}}) RETURN properties(n) AS props",
            {"uuid": uuid},
        )
        return records[0]["props"] if records else None

    def _edge_exists(self, edge):
        records = self.client.run_cypher(
            f"MATCH (:{edge['from_label']} {{uuid: $f}})"
            f"-[r:{edge['relation']}]->"
            f"(:{edge['to_label']} {{uuid: $t}}) RETURN r LIMIT 1",
            {"f": edge["from_uuid"], "t": edge["to_uuid"]},
        )
        return bool(records)

    def diff_slice(self, label, obj):
        """Annotate the desired slice with per-node / per-edge status.

        node status: ``new`` | ``changed`` | ``existing`` (checksum match).
        edge status: ``new`` | ``existing``.
        """
        root, nodes, edges = self.desired_slice(label, obj)
        node_status = {}
        for key, node in nodes.items():
            existing = self.read_node(node["label"], node["uuid"])
            if existing is None:
                node_status[key] = "new"
            elif existing.get("_checksum") == node["checksum"]:
                node_status[key] = "existing"
            else:
                node_status[key] = "changed"
        edge_status = {
            key: ("existing" if self._edge_exists(edge) else "new")
            for key, edge in edges.items()
        }
        return root, nodes, edges, node_status, edge_status

    # -- preview (Cytoscape elements) ----------------------------------

    def preview(self, label, obj):
        root, nodes, edges, node_status, edge_status = self.diff_slice(label, obj)
        root_key = (root["label"], root["uuid"])

        cy_nodes = [
            {
                "id": f"{node['label']}:{node['uuid']}",
                "labels": [node["label"]],
                "props": node["props"],
                "status": node_status[key],
                "is_root": key == root_key,
            }
            for key, node in nodes.items()
        ]
        cy_edges = [
            {
                "id": "e:" + "|".join(key),
                "start": f"{edge['from_label']}:{edge['from_uuid']}",
                "end": f"{edge['to_label']}:{edge['to_uuid']}",
                "type": edge["relation"],
                "status": edge_status[key],
            }
            for key, edge in edges.items()
        ]
        summary = {
            "new_nodes": sum(s == "new" for s in node_status.values()),
            "changed_nodes": sum(s == "changed" for s in node_status.values()),
            "existing_nodes": sum(s == "existing" for s in node_status.values()),
            "new_edges": sum(s == "new" for s in edge_status.values()),
            "existing_edges": sum(s == "existing" for s in edge_status.values()),
        }
        return cy_nodes, cy_edges, summary

    # -- apply ----------------------------------------------------------

    def apply(self, label, obj):
        root, nodes, edges, node_status, _edge_status = self.diff_slice(label, obj)
        result = {"created": 0, "updated": 0, "unchanged": 0, "archived": 0}

        for key, node in nodes.items():
            status = node_status[key]
            if status == "new":
                self._create_node(node)
                result["created"] += 1
            elif status == "changed":
                self._archive_then_update(node)
                result["updated"] += 1
                result["archived"] += 1
            else:
                result["unchanged"] += 1

        self._sync_outgoing_edges(root, edges)
        return result

    def _create_node(self, node):
        props = neo4j_props(node["props"])
        props["_checksum"] = node["checksum"]
        self.client.run_cypher(
            f"MERGE (n:{node['label']} {{uuid: $uuid}}) SET n += $props",
            {"uuid": node["uuid"], "props": props},
        )

    def _archive_then_update(self, node):
        label = node["label"]
        uuid = node["uuid"]

        # 1. Snapshot the current (old) state into a :HISTORICAL child node with
        #    a fresh uuid; the canonical uuid is preserved in prev_uuid.
        existing = self.read_node(label, uuid) or {}
        archived_at = time.time()
        child_uuid = f"{uuid}~{int(archived_at * 1000)}-{uuid_lib.uuid4().hex[:6]}"
        snapshot = {k: v for k, v in existing.items() if k != "uuid"}
        snapshot.update(
            {
                "uuid": child_uuid,
                "prev_uuid": uuid,
                "_historical": True,
                "archived_at": archived_at,
            }
        )
        self.client.run_cypher(
            f"CREATE (h:{label} $props)", {"props": neo4j_props(snapshot)}
        )
        self.client.run_cypher(
            f"MATCH (n:{label} {{uuid: $uuid}}) "
            f"MATCH (h:{label} {{uuid: $child}}) "
            f"MERGE (n)-[:{HISTORICAL_REL}]->(h)",
            {"uuid": uuid, "child": child_uuid},
        )

        # 2. Enforce the per-node history cap (newest kept).
        self._prune_history(label, uuid)

        # 3. Update the canonical node in place.
        props = neo4j_props(node["props"])
        props["_checksum"] = node["checksum"]
        self.client.run_cypher(
            f"MATCH (n:{label} {{uuid: $uuid}}) SET n += $props",
            {"uuid": uuid, "props": props},
        )

    def _prune_history(self, label, uuid):
        self.client.run_cypher(
            f"MATCH (n:{label} {{uuid: $uuid}})-[:{HISTORICAL_REL}]->(h) "
            "WITH h ORDER BY h.archived_at DESC SKIP $keep "
            "DETACH DELETE h",
            {"uuid": uuid, "keep": max_history()},
        )

    def _sync_outgoing_edges(self, root, edges):
        """MERGE the root's desired edges and drop stale ones.

        Covers every *declared* outgoing relation of the root's label (not just
        relations with a desired edge) so a now-null FK gets its edge removed.
        Never touches ``:HISTORICAL`` (a different relationship type).
        """
        label = root["label"]
        desired = {}
        for edge in edges.values():
            desired.setdefault(edge["relation"], []).append(edge)

        declared = {
            link["relation"]
            for link in self._links_by_from_label.get(label, [])
            if not link.get("via_model")
        }
        for rel in declared:
            keep = [edge["to_uuid"] for edge in desired.get(rel, [])]
            self.client.run_cypher(
                f"MATCH (a:{label} {{uuid: $uuid}})-[r:{rel}]->(b) "
                "WHERE NOT b.uuid IN $keep DELETE r",
                {"uuid": root["uuid"], "keep": keep},
            )

        for rel, rel_edges in desired.items():
            for edge in rel_edges:
                self.client.run_cypher(
                    f"MATCH (a:{edge['from_label']} {{uuid: $f}}) "
                    f"MATCH (b:{edge['to_label']} {{uuid: $t}}) "
                    f"MERGE (a)-[:{rel}]->(b)",
                    {"f": edge["from_uuid"], "t": edge["to_uuid"]},
                )
