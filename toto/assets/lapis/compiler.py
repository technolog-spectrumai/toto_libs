from __future__ import annotations

from typing import Any

from .exceptions import LapisValidationError

ALLOWED_NODE_TYPES = {
    "seq", "assert", "if", "approve", "reject", "return",
    "uint64", "bytes", "bool",
    "transaction", "group_transaction", "global", "app_arg",
    "app_global_get", "app_global_put", "app_local_get", "app_local_put",
    "box_get", "box_put", "box_del", "box_len", "box_extract", "box_replace",
    "inner_transaction_begin", "inner_transaction_set", "inner_transaction_submit",
    "eq", "neq", "gt", "gte", "lt", "lte", "and", "or", "not",
    "add", "sub", "mul", "div", "mod",
    "concat", "extract", "len", "itob", "btoi", "sha256",
    "log",
}

_REMOVED_NODE_TYPES = {
    "decimal", "get_state", "set_state", "account", "asset", "amount",
    "balance", "transfer", "oblig", "record", "int", "param",
    "txn", "gtxn", "inner_begin", "inner_set", "inner_submit",
}


class LapisCompiler:
    """Validates canonical Lapis (target: teal) contracts and extracts action bodies."""

    def validate_contract(self, tree: dict[str, Any]) -> None:
        if not isinstance(tree, dict):
            raise LapisValidationError("Lapis tree must be an object.")
        if tree.get("language") != "lapis":
            raise LapisValidationError("Lapis tree must include language: lapis")
        if tree.get("version") != 1:
            raise LapisValidationError("Unsupported Lapis version; expected 1.")
        actions = tree.get("actions")
        if not isinstance(actions, dict) or not actions:
            raise LapisValidationError("Lapis contract requires non-empty actions.")
        for name, action_def in actions.items():
            if not isinstance(name, str) or not name:
                raise LapisValidationError("Action names must be non-empty strings.")
            if not isinstance(action_def, dict) or "body" not in action_def:
                raise LapisValidationError(f"Action '{name}' must have a body key.")
            self.validate_node(action_def["body"])

    def compile_action(self, tree: dict[str, Any], action: str) -> dict[str, Any]:
        self.validate_contract(tree)
        try:
            return tree["actions"][action]["body"]
        except KeyError as exc:
            raise LapisValidationError(f"Unknown Lapis action: {action}") from exc

    def validate_node(self, node: Any) -> None:
        if not isinstance(node, dict):
            raise LapisValidationError("Lapis node must be an object.")
        node_type = node.get("type")

        if node_type in _REMOVED_NODE_TYPES:
            raise LapisValidationError(
                f"Node type {node_type!r} is not supported in canonical Lapis (target: teal). "
                f"Use app_global_get/put, inner_transaction_*, log, uint64 instead."
            )

        if node_type not in ALLOWED_NODE_TYPES:
            raise LapisValidationError(f"Unsupported Lapis node type: {node_type!r}")

        if node_type == "seq":
            steps = node.get("steps")
            if not isinstance(steps, list):
                raise LapisValidationError("seq requires steps list.")
            for step in steps:
                self.validate_node(step)
            return

        if node_type == "assert":
            self.validate_node(node.get("condition"))
            return

        if node_type == "if":
            self.validate_node(node.get("condition"))
            self.validate_node(node.get("then"))
            if "else" in node:
                self.validate_node(node["else"])
            return

        if node_type in {"approve", "reject", "inner_transaction_begin", "inner_transaction_submit"}:
            return

        if node_type == "return":
            self.validate_node(node.get("value"))
            return

        if node_type in {"uint64", "bytes", "bool"}:
            if "value" not in node:
                raise LapisValidationError(f"{node_type} requires value.")
            return

        if node_type == "transaction":
            if not node.get("field"):
                raise LapisValidationError("transaction requires field.")
            return

        if node_type == "group_transaction":
            if "index" not in node:
                raise LapisValidationError("group_transaction requires index.")
            if not node.get("field"):
                raise LapisValidationError("group_transaction requires field.")
            return

        if node_type == "global":
            if not node.get("field"):
                raise LapisValidationError("global requires field.")
            return

        if node_type == "app_arg":
            if "index" not in node:
                raise LapisValidationError("app_arg requires index.")
            return

        if node_type == "app_global_get":
            self.validate_node(node.get("key"))
            return

        if node_type == "app_global_put":
            self.validate_node(node.get("key"))
            self.validate_node(node.get("value"))
            return

        if node_type == "app_local_get":
            self.validate_node(node.get("account"))
            self.validate_node(node.get("key"))
            return

        if node_type == "app_local_put":
            self.validate_node(node.get("account"))
            self.validate_node(node.get("key"))
            self.validate_node(node.get("value"))
            return

        if node_type in {"box_get", "box_del", "box_len"}:
            self.validate_node(node.get("name"))
            return

        if node_type == "box_put":
            self.validate_node(node.get("name"))
            self.validate_node(node.get("value"))
            return

        if node_type == "box_extract":
            self.validate_node(node.get("name"))
            self.validate_node(node.get("start"))
            self.validate_node(node.get("length"))
            return

        if node_type == "box_replace":
            self.validate_node(node.get("name"))
            self.validate_node(node.get("start"))
            self.validate_node(node.get("value"))
            return

        if node_type == "inner_transaction_set":
            if not node.get("field"):
                raise LapisValidationError("inner_transaction_set requires field.")
            self.validate_node(node.get("value"))
            return

        if node_type in {"eq", "neq", "gt", "gte", "lt", "lte", "add", "sub", "mul", "div", "mod", "concat"}:
            self.validate_node(node.get("left"))
            self.validate_node(node.get("right"))
            return

        if node_type in {"and", "or"}:
            values = node.get("values")
            if not isinstance(values, list) or not values:
                raise LapisValidationError(f"{node_type} requires non-empty values list.")
            for value in values:
                self.validate_node(value)
            return

        if node_type == "not":
            self.validate_node(node.get("value"))
            return

        if node_type in {"len", "itob", "btoi", "sha256", "log", "return"}:
            self.validate_node(node.get("value"))
            return

        if node_type == "extract":
            self.validate_node(node.get("value"))
            self.validate_node(node.get("start"))
            self.validate_node(node.get("length"))
            return
