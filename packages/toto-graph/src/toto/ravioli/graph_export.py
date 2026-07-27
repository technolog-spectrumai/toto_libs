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
     uuid, old uuid kept in ``prev_uuid``). History is never capped here —
     pruning is a separate, manual review-then-apply step
     (``ravioli.services.graph_plans.create_prune_plan``).

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


HISTORICAL_REL = "_HISTORICAL"

# Properties ravioli manages itself — excluded from the content checksum so that
# bookkeeping never looks like a content change.
RESERVED_PROPS = {"uuid", "_checksum", "_historical", "_prev_uuid", "_archived_at"}

# Marker-prop prefixes other curation apps own (formica pheromone/quarantine
# markers). Excluded from the checksum the same way: a pheromone deposit must
# never read as a content change and trigger a spurious re-sync.
RESERVED_PREFIXES = ("_ph", "_formica")

DEFAULT_EXCLUDED_APPS = ["workflows", "fileservices", "vault"]


# ---------------------------------------------------------------------------
# Settings-backed knobs
# ---------------------------------------------------------------------------

def excluded_apps():
    return set(getattr(settings, "RAVIOLI_EXPORT_EXCLUDED_APPS", DEFAULT_EXCLUDED_APPS))


def is_app_excluded(app_label):
    return app_label in excluded_apps()


# ---------------------------------------------------------------------------
# Content checksum
# ---------------------------------------------------------------------------

def content_checksum(props):
    """Stable SHA-256 of a node's mapped (content) properties."""
    payload = json.dumps(
        {
            k: normalize(v)
            for k, v in (props or {}).items()
            if k not in RESERVED_PROPS and not k.startswith(RESERVED_PREFIXES)
        },
        sort_keys=True,
        cls=DjangoJSONEncoder,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def archive_node_to_history(client, label, uuid):
    """Snapshot a node's current state into a ``:HISTORICAL`` child node.

    Copies the live node's properties into a new node (fresh ``uuid``, the
    original kept in ``prev_uuid``, ``_historical=true``, ``archived_at``) and
    links ``(canonical)-[:HISTORICAL]->(snapshot)``. Does **not** modify the
    canonical node — callers update it afterwards. No-op if the node is missing.

    Shared by the per-object export and the "staging" bulk sync so both grow the
    same history (visible/prunable in the History tab).
    """
    records = client.run_cypher(
        f"MATCH (n:{label} {{uuid: $uuid}}) RETURN properties(n) AS props",
        {"uuid": uuid},
    )
    if not records or records[0]["props"] is None:
        return
    archived_at = time.time()
    child_uuid = f"{uuid}~{int(archived_at * 1000)}-{uuid_lib.uuid4().hex[:6]}"
    snapshot = {k: v for k, v in records[0]["props"].items() if k != "uuid"}
    snapshot.update(
        {"uuid": child_uuid, "_prev_uuid": uuid, "_historical": True, "_archived_at": archived_at}
    )
    client.run_cypher(f"CREATE (h:{label} $props)", {"props": neo4j_props(snapshot)})
    client.run_cypher(
        f"MATCH (n:{label} {{uuid: $uuid}}) MATCH (h:{label} {{uuid: $child}}) "
        f"MERGE (n)-[:{HISTORICAL_REL}]->(h)",
        {"uuid": uuid, "child": child_uuid},
    )


def relink_orphan_history(client):
    """Reconnect orphaned snapshots to their canonical node via :HISTORICAL.

    A full sync that deletes a canonical node ``DETACH DELETE``\\s its
    ``:HISTORICAL`` edges, orphaning its snapshots. If the canonical later
    reappears (same uuid, e.g. the row is re-added to SQL), this rebuilds the
    edge. Idempotent (``MERGE``); returns the number of edges (re)created.
    Snapshots whose canonical no longer exists stay orphaned — prune them.
    """
    records = client.run_cypher(
        "MATCH (h) WHERE h._historical = true AND NOT ()-[:" + HISTORICAL_REL + "]->(h) "
        "MATCH (c {uuid: h._prev_uuid}) "
        "WHERE coalesce(c._historical, false) = false "
        "AND any(l IN labels(c) WHERE l IN labels(h)) "
        "MERGE (c)-[:" + HISTORICAL_REL + "]->(h) "
        "RETURN count(*) AS n"
    )
    return records[0]["n"] if records else 0


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
        self._model_by_label = {}          # label -> model class
        self._links_by_from_label = {}
        for config in self.configs:
            app = config.get("app", "")
            if is_app_excluded(app):
                continue                    # workflows / services / files: never mapped
            for node in config.get("nodes", []):
                label = node["label"]
                self._node_def_by_label[label] = node
                model = import_model(node["model"])
                self._model_to_label[model] = (label, node.get("uuid_field", "uid"))
                self._model_by_label[label] = model
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
            to_model = self._model_by_label.get(to_label)

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
                # Skip cross-model FKs (e.g. an `assignee` FK to Practitioner
                # declared as `-> Person`): its uuid matches no Person node, so
                # the edge could never be created.
                if to_model is not None and not isinstance(related, to_model):
                    continue
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

        # Snapshot the current (old) state into a :HISTORICAL child, then update
        # the canonical node in place. (History is kept unpruned for now — prune
        # manually via the History tab.)
        archive_node_to_history(self.client, label, uuid)
        props = neo4j_props(node["props"])
        props["_checksum"] = node["checksum"]
        self.client.run_cypher(
            f"MATCH (n:{label} {{uuid: $uuid}}) SET n += $props",
            {"uuid": uuid, "props": props},
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
