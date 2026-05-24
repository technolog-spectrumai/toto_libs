from __future__ import annotations

from typing import Any

from .exceptions import LapisValidationError

ALLOWED_NODE_TYPES = {
    "seq", "assert", "if",
    "eq", "neq", "gt", "gte", "lt", "lte",
    "and", "or", "not",
    "add", "sub", "mul", "div",
    "int", "decimal", "bytes", "bool",
    "param", "get_state", "set_state",
    "account", "asset", "amount", "balance",
    "transfer", "oblig", "record",
}


class LapisCompiler:
    """Validates Lapis JSON/YAML trees and extracts an action execution plan."""

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
        for name, node in actions.items():
            if not isinstance(name, str) or not name:
                raise LapisValidationError("Action names must be non-empty strings.")
            self.validate_node(node)

    def compile_action(self, tree: dict[str, Any], action: str) -> dict[str, Any]:
        self.validate_contract(tree)
        try:
            return tree["actions"][action]
        except KeyError as exc:
            raise LapisValidationError(f"Unknown Lapis action: {action}") from exc

    def validate_node(self, node: Any) -> None:
        if not isinstance(node, dict):
            raise LapisValidationError("Lapis node must be an object.")
        node_type = node.get("type")
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

        if node_type in {"eq", "neq", "gt", "gte", "lt", "lte", "add", "sub", "mul", "div"}:
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

        if node_type in {"int", "decimal", "bytes", "bool"}:
            if "value" not in node:
                raise LapisValidationError(f"{node_type} requires value.")
            return

        if node_type == "param":
            if not node.get("name"):
                raise LapisValidationError("param requires name.")
            return

        if node_type == "get_state":
            if not node.get("key"):
                raise LapisValidationError("get_state requires key.")
            return

        if node_type == "set_state":
            if not node.get("key"):
                raise LapisValidationError("set_state requires key.")
            self.validate_node(node.get("value"))
            return

        if node_type in {"account", "asset", "amount"}:
            if not node.get("ref"):
                raise LapisValidationError(f"{node_type} requires ref.")
            return

        if node_type == "balance":
            self.validate_node(node.get("account"))
            self.validate_node(node.get("asset"))
            return

        if node_type == "transfer":
            for key in ["asset", "from", "to", "amount"]:
                if key not in node:
                    raise LapisValidationError(f"transfer requires {key}.")
                self.validate_node(node[key])
            return

        if node_type == "record":
            if not node.get("kind"):
                raise LapisValidationError("record requires kind.")
            return

        if node_type == "oblig":
            for key in ["debtor", "creditor", "asset", "amount", "due_at"]:
                if key not in node:
                    raise LapisValidationError(f"oblig requires {key}.")
                if key in {"debtor", "creditor", "asset", "amount"}:
                    self.validate_node(node[key])
            return
