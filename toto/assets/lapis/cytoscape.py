from __future__ import annotations

from typing import Any


def lapis_to_cytoscape_tree(lapis: dict) -> dict:
    """Convert a parsed Lapis contract dict into Cytoscape.js nodes/edges for AST visualization."""
    nodes = []
    edges = []
    counter = [0]

    def _id() -> str:
        counter[0] += 1
        return f"n{counter[0]}"

    def _node(node_id: str, label: str, node_type: str, extra: str = "") -> None:
        nodes.append({"data": {"id": node_id, "label": label, "type": node_type, "extra": extra}})

    def _edge(src: str, dst: str, label: str = "") -> None:
        edges.append({"data": {"id": f"e-{src}-{dst}", "source": src, "target": dst, "label": label}})

    def _walk(node: Any, parent_id: str | None, edge_label: str = "") -> str | None:
        if not isinstance(node, dict):
            nid = _id()
            _node(nid, repr(node), "literal")
            if parent_id:
                _edge(parent_id, nid, edge_label)
            return nid

        ntype = node.get("type", "?")
        label = ntype

        if ntype in ("uint64", "bytes", "bool"):
            label = f"{ntype}: {node.get('value', '')}"
        elif ntype == "transaction":
            label = f"transaction.{node.get('field', '')}"
        elif ntype == "group_transaction":
            label = f"gtxn[{node.get('index', '?')}].{node.get('field', '')}"
        elif ntype == "global":
            label = f"global.{node.get('field', '')}"
        elif ntype == "app_arg":
            label = f"app_arg[{node.get('index', '?')}]"
        elif ntype == "inner_transaction_set":
            label = f"inner_set.{node.get('field', '')}"

        nid = _id()
        _node(nid, label, ntype)
        if parent_id:
            _edge(parent_id, nid, edge_label)

        if ntype == "seq":
            for i, step in enumerate(node.get("steps", [])):
                _walk(step, nid, str(i))
        elif ntype == "assert":
            _walk(node.get("condition"), nid, "condition")
        elif ntype == "if":
            _walk(node.get("condition"), nid, "condition")
            _walk(node.get("then"), nid, "then")
            if "else" in node:
                _walk(node["else"], nid, "else")
        elif ntype == "return":
            _walk(node.get("value"), nid, "value")
        elif ntype in ("and", "or"):
            for i, v in enumerate(node.get("values", [])):
                _walk(v, nid, str(i))
        elif ntype == "not":
            _walk(node.get("value"), nid, "value")
        elif ntype in ("eq", "neq", "gt", "gte", "lt", "lte", "add", "sub", "mul", "div", "mod", "concat"):
            _walk(node.get("left"), nid, "left")
            _walk(node.get("right"), nid, "right")
        elif ntype in ("len", "itob", "btoi", "sha256", "log"):
            _walk(node.get("value"), nid, "value")
        elif ntype == "extract":
            _walk(node.get("value"), nid, "value")
            _walk(node.get("start"), nid, "start")
            _walk(node.get("length"), nid, "length")
        elif ntype == "app_global_get":
            _walk(node.get("key"), nid, "key")
        elif ntype == "app_global_put":
            _walk(node.get("key"), nid, "key")
            _walk(node.get("value"), nid, "value")
        elif ntype == "app_local_get":
            _walk(node.get("account"), nid, "account")
            _walk(node.get("key"), nid, "key")
        elif ntype == "app_local_put":
            _walk(node.get("account"), nid, "account")
            _walk(node.get("key"), nid, "key")
            _walk(node.get("value"), nid, "value")
        elif ntype in ("box_get", "box_del", "box_len"):
            _walk(node.get("name"), nid, "name")
        elif ntype == "box_put":
            _walk(node.get("name"), nid, "name")
            _walk(node.get("value"), nid, "value")
        elif ntype == "box_extract":
            _walk(node.get("name"), nid, "name")
            _walk(node.get("start"), nid, "start")
            _walk(node.get("length"), nid, "length")
        elif ntype == "box_replace":
            _walk(node.get("name"), nid, "name")
            _walk(node.get("start"), nid, "start")
            _walk(node.get("value"), nid, "value")
        elif ntype == "inner_transaction_set":
            _walk(node.get("value"), nid, "value")

        return nid

    actions = lapis.get("actions", {})
    root_id = _id()
    _node(root_id, "contract", "root")

    for action_name, action_def in actions.items():
        action_id = _id()
        _node(action_id, action_name, "action")
        _edge(root_id, action_id)
        if isinstance(action_def, dict) and "body" in action_def:
            _walk(action_def["body"], action_id, "body")

    return {"nodes": nodes, "edges": edges}
