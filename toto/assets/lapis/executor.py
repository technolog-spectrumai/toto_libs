from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from .exceptions import LapisExecutionError


@dataclass
class LapisContext:
    agreement: Any
    params: dict[str, Any]
    state: dict[str, Any]
    accounts: dict[str, Any]
    assets: dict[str, Any]
    metadata: dict[str, Any]
    transfer: Callable[..., Any]
    create_obligation: Callable[..., Any]
    record: Callable[..., Any]
    balance: Callable[..., int]


class LapisExecutor:
    def execute(self, plan: dict[str, Any], ctx: LapisContext) -> Any:
        return self.eval_node(plan, ctx)

    def eval_node(self, node: dict[str, Any], ctx: LapisContext) -> Any:
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
            return self.eval_node(node["then"], ctx) if self.eval_node(node["condition"], ctx) else self.eval_node(node["else"], ctx) if "else" in node else None

        if t == "eq": return self.eval_node(node["left"], ctx) == self.eval_node(node["right"], ctx)
        if t == "neq": return self.eval_node(node["left"], ctx) != self.eval_node(node["right"], ctx)
        if t == "gt": return self.eval_node(node["left"], ctx) > self.eval_node(node["right"], ctx)
        if t == "gte": return self.eval_node(node["left"], ctx) >= self.eval_node(node["right"], ctx)
        if t == "lt": return self.eval_node(node["left"], ctx) < self.eval_node(node["right"], ctx)
        if t == "lte": return self.eval_node(node["left"], ctx) <= self.eval_node(node["right"], ctx)
        if t == "and": return all(self.eval_node(v, ctx) for v in node["values"])
        if t == "or": return any(self.eval_node(v, ctx) for v in node["values"])
        if t == "not": return not self.eval_node(node["value"], ctx)

        if t == "add": return self.eval_node(node["left"], ctx) + self.eval_node(node["right"], ctx)
        if t == "sub": return self.eval_node(node["left"], ctx) - self.eval_node(node["right"], ctx)
        if t == "mul": return self.eval_node(node["left"], ctx) * self.eval_node(node["right"], ctx)
        if t == "div":
            right = self.eval_node(node["right"], ctx)
            if right == 0:
                raise LapisExecutionError("Division by zero.")
            return self.eval_node(node["left"], ctx) / right

        if t == "int": return int(node["value"])
        if t == "decimal": return Decimal(str(node["value"]))
        if t == "bytes": return str(node["value"])
        if t == "bool": return bool(node["value"])

        if t == "param":
            name = node["name"]
            if name not in ctx.params:
                raise LapisExecutionError(f"Missing param: {name}")
            return ctx.params[name]
        if t == "get_state":
            return ctx.state.get(node["key"])
        if t == "set_state":
            value = self.eval_node(node["value"], ctx)
            ctx.state[node["key"]] = value
            return value

        if t == "account":
            ref = node["ref"]
            if ref not in ctx.accounts:
                raise LapisExecutionError(f"Unknown account ref: {ref}")
            return ctx.accounts[ref]
        if t == "asset":
            ref = node["ref"]
            if ref not in ctx.assets:
                raise LapisExecutionError(f"Unknown asset ref: {ref}")
            return ctx.assets[ref]
        if t == "amount":
            ref = node["ref"]
            if ref not in ctx.metadata:
                raise LapisExecutionError(f"Unknown amount ref: {ref}")
            return int(ctx.metadata[ref])
        if t == "balance":
            return ctx.balance(account=self.eval_node(node["account"], ctx), asset=self.eval_node(node["asset"], ctx))

        if t == "transfer":
            amount = self.eval_node(node["amount"], ctx)
            if amount <= 0:
                raise LapisExecutionError("Transfer amount must be positive.")
            return ctx.transfer(
                agreement=ctx.agreement,
                source_account=self.eval_node(node["from"], ctx),
                target_account=self.eval_node(node["to"], ctx),
                asset=self.eval_node(node["asset"], ctx),
                amount_base_units=amount,
                metadata={"lapis": True},
            )
        if t == "record":
            return ctx.record(agreement=ctx.agreement, kind=node["kind"], data=self.resolve_data(node.get("data", {}), ctx))
        if t == "oblig":
            return ctx.create_obligation(
                agreement=ctx.agreement,
                debtor_account=self.eval_node(node["debtor"], ctx),
                creditor_account=self.eval_node(node["creditor"], ctx),
                asset=self.eval_node(node["asset"], ctx),
                amount_base_units=self.eval_node(node["amount"], ctx),
                due_at=self.resolve_data(node["due_at"], ctx),
                role=node.get("role", ""),
                metadata=node.get("metadata", {}),
            )
        raise LapisExecutionError(f"Unhandled node type: {t}")

    def resolve_data(self, value: Any, ctx: LapisContext) -> Any:
        if isinstance(value, dict) and "type" in value:
            return self.eval_node(value, ctx)
        if isinstance(value, dict):
            return {k: self.resolve_data(v, ctx) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve_data(v, ctx) for v in value]
        return value
