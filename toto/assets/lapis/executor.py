from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .exceptions import LapisExecutionError


@dataclass
class LapisContext:
    global_state: dict[str, Any]
    local_state: dict[str, Any]          # keyed by account string
    boxes: dict[str, Any]
    transaction: dict[str, Any]
    group_transactions: list[dict]
    global_fields: dict[str, Any]
    app_args: list[Any]
    inner_transactions: list[dict] = field(default_factory=list)
    logs: list[Any] = field(default_factory=list)
    result: str | None = None
    pending_inner_txn: dict | None = field(default=None, repr=False)


class LapisExecutor:
    def execute(self, plan: dict[str, Any], ctx: LapisContext) -> Any:
        return self.eval_node(plan, ctx)

    def eval_node(self, node: dict[str, Any], ctx: LapisContext) -> Any:  # noqa: C901
        t = node["type"]

        if t == "seq":
            result = None
            for step in node["steps"]:
                result = self.eval_node(step, ctx)
            return result

        if t == "assert":
            if not self.eval_node(node["condition"], ctx):
                raise LapisExecutionError("Lapis assertion failed.")
            return True

        if t == "if":
            if self.eval_node(node["condition"], ctx):
                return self.eval_node(node["then"], ctx)
            return self.eval_node(node["else"], ctx) if "else" in node else None

        if t == "approve":
            ctx.result = "approve"
            return True

        if t == "reject":
            ctx.result = "reject"
            return False

        if t == "return":
            val = self.eval_node(node["value"], ctx)
            ctx.result = str(val)
            return val

        if t == "uint64":
            return int(node["value"])

        if t == "bytes":
            return str(node["value"])

        if t == "bool":
            return bool(node["value"])

        if t == "transaction":
            return ctx.transaction.get(node["field"])

        if t == "group_transaction":
            idx = node["index"]
            if idx < 0 or idx >= len(ctx.group_transactions):
                raise LapisExecutionError(f"group_transaction index {idx} out of range.")
            return ctx.group_transactions[idx].get(node["field"])

        if t == "global":
            return ctx.global_fields.get(node["field"])

        if t == "app_arg":
            idx = node["index"]
            if idx < 0 or idx >= len(ctx.app_args):
                raise LapisExecutionError(f"app_arg index {idx} out of range.")
            return ctx.app_args[idx]

        if t == "app_global_get":
            key = self.eval_node(node["key"], ctx)
            return ctx.global_state.get(key)

        if t == "app_global_put":
            key = self.eval_node(node["key"], ctx)
            val = self.eval_node(node["value"], ctx)
            ctx.global_state[key] = val
            return val

        if t == "app_local_get":
            account = self.eval_node(node["account"], ctx)
            key = self.eval_node(node["key"], ctx)
            return ctx.local_state.get(str(account), {}).get(key)

        if t == "app_local_put":
            account = str(self.eval_node(node["account"], ctx))
            key = self.eval_node(node["key"], ctx)
            val = self.eval_node(node["value"], ctx)
            ctx.local_state.setdefault(account, {})[key] = val
            return val

        if t == "box_get":
            name = self.eval_node(node["name"], ctx)
            val = ctx.boxes.get(name)
            return {"value": val, "exists": name in ctx.boxes}

        if t == "box_put":
            name = self.eval_node(node["name"], ctx)
            val = self.eval_node(node["value"], ctx)
            ctx.boxes[name] = val
            return True

        if t == "box_del":
            name = self.eval_node(node["name"], ctx)
            ctx.boxes.pop(name, None)
            return True

        if t == "box_len":
            name = self.eval_node(node["name"], ctx)
            val = ctx.boxes.get(name)
            return len(str(val)) if val is not None else 0

        if t == "box_extract":
            name = self.eval_node(node["name"], ctx)
            start = self.eval_node(node["start"], ctx)
            length = self.eval_node(node["length"], ctx)
            val = str(ctx.boxes.get(name, ""))
            return val[start: start + length]

        if t == "box_replace":
            name = self.eval_node(node["name"], ctx)
            start = self.eval_node(node["start"], ctx)
            val = self.eval_node(node["value"], ctx)
            existing = str(ctx.boxes.get(name, ""))
            replacement = str(val)
            ctx.boxes[name] = existing[:start] + replacement + existing[start + len(replacement):]
            return True

        if t == "inner_transaction_begin":
            ctx.pending_inner_txn = {}
            return None

        if t == "inner_transaction_set":
            if ctx.pending_inner_txn is None:
                raise LapisExecutionError("inner_transaction_set without begin.")
            ctx.pending_inner_txn[node["field"]] = self.eval_node(node["value"], ctx)
            return None

        if t == "inner_transaction_submit":
            if ctx.pending_inner_txn is None:
                raise LapisExecutionError("inner_transaction_submit without begin.")
            ctx.inner_transactions.append(ctx.pending_inner_txn)
            ctx.pending_inner_txn = None
            return None

        if t == "eq":
            return self.eval_node(node["left"], ctx) == self.eval_node(node["right"], ctx)
        if t == "neq":
            return self.eval_node(node["left"], ctx) != self.eval_node(node["right"], ctx)
        if t == "gt":
            return self.eval_node(node["left"], ctx) > self.eval_node(node["right"], ctx)
        if t == "gte":
            return self.eval_node(node["left"], ctx) >= self.eval_node(node["right"], ctx)
        if t == "lt":
            return self.eval_node(node["left"], ctx) < self.eval_node(node["right"], ctx)
        if t == "lte":
            return self.eval_node(node["left"], ctx) <= self.eval_node(node["right"], ctx)
        if t == "and":
            return all(self.eval_node(v, ctx) for v in node["values"])
        if t == "or":
            return any(self.eval_node(v, ctx) for v in node["values"])
        if t == "not":
            return not self.eval_node(node["value"], ctx)

        if t == "add":
            return self.eval_node(node["left"], ctx) + self.eval_node(node["right"], ctx)
        if t == "sub":
            return self.eval_node(node["left"], ctx) - self.eval_node(node["right"], ctx)
        if t == "mul":
            return self.eval_node(node["left"], ctx) * self.eval_node(node["right"], ctx)
        if t == "div":
            right = self.eval_node(node["right"], ctx)
            if right == 0:
                raise LapisExecutionError("Division by zero.")
            return self.eval_node(node["left"], ctx) // right
        if t == "mod":
            right = self.eval_node(node["right"], ctx)
            if right == 0:
                raise LapisExecutionError("Modulo by zero.")
            return self.eval_node(node["left"], ctx) % right

        if t == "concat":
            return str(self.eval_node(node["left"], ctx)) + str(self.eval_node(node["right"], ctx))

        if t == "extract":
            val = str(self.eval_node(node["value"], ctx))
            start = self.eval_node(node["start"], ctx)
            length = self.eval_node(node["length"], ctx)
            return val[start: start + length]

        if t == "len":
            return len(str(self.eval_node(node["value"], ctx)))

        if t == "itob":
            val = int(self.eval_node(node["value"], ctx))
            return val.to_bytes(8, "big").hex()

        if t == "btoi":
            val = self.eval_node(node["value"], ctx)
            if isinstance(val, int):
                return val
            return int(bytes.fromhex(str(val)), 16) if val else 0

        if t == "sha256":
            val = str(self.eval_node(node["value"], ctx)).encode()
            return hashlib.sha256(val).hexdigest()

        if t == "log":
            val = self.eval_node(node["value"], ctx)
            ctx.logs.append(val)
            return val

        raise LapisExecutionError(f"Unhandled node type: {t}")
