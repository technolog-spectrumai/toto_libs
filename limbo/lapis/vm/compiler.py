from __future__ import annotations

from .exceptions import LapisValidationError

VALID_STEP_TYPES = {
    "start", "condition", "payment", "record", "state",
    "stop", "approval", "notification",
}


class ContractFlowValidator:
    """Validates Contract Flow YAML documents (kind: contract_flow)."""

    def validate(self, tree: dict) -> None:
        if not isinstance(tree, dict):
            raise LapisValidationError("Contract Flow must be a YAML object.")
        if tree.get("language") != "lapis":
            raise LapisValidationError("Must include language: lapis")
        if tree.get("version") != 1:
            raise LapisValidationError("Unsupported version; expected 1.")
        if tree.get("kind") != "contract_flow":
            raise LapisValidationError("Must include kind: contract_flow")
        steps = tree.get("steps")
        if not isinstance(steps, list) or not steps:
            raise LapisValidationError("Contract Flow requires a non-empty steps list.")
        ids = set()
        for step in steps:
            if not isinstance(step, dict):
                raise LapisValidationError("Each step must be a YAML object.")
            step_id = step.get("id")
            if not step_id or not isinstance(step_id, str):
                raise LapisValidationError("Each step must have a non-empty string id.")
            if step_id in ids:
                raise LapisValidationError(f"Duplicate step id: {step_id!r}")
            ids.add(step_id)
            step_type = step.get("type")
            if step_type not in VALID_STEP_TYPES:
                raise LapisValidationError(
                    f"Step {step_id!r} has unsupported type: {step_type!r}. "
                    f"Valid types: {', '.join(sorted(VALID_STEP_TYPES))}"
                )
            if not step.get("title"):
                raise LapisValidationError(f"Step {step_id!r} must have a title.")

    def get_steps(self, tree: dict) -> list[dict]:
        self.validate(tree)
        return tree.get("steps", [])


# Keep old name as alias so any external imports don't crash immediately.
LapisCompiler = ContractFlowValidator
