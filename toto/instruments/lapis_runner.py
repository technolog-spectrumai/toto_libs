"""
Lapis execution gate for financial instrument actions.

`gate(instrument, action_name)` runs the instrument's linked Lapis contract
for the given action inside the caller's transaction.  If the contract
approves, global_state is persisted back to the Contract row.

Usage (always inside transaction.atomic):

    with transaction.atomic():
        lapis_runner.gate(instrument, "activate")
        SomeService.activate(sub)
"""
from __future__ import annotations

import logging

from toto.assets.lapis.compiler import LapisCompiler
from toto.assets.lapis.exceptions import LapisExecutionError
from toto.assets.lapis.executor import LapisContext, LapisExecutor
from toto.assets.lapis.loader import loads_contract

logger = logging.getLogger(__name__)


def gate(instrument, action_name: str) -> LapisContext:
    """
    Run the Lapis action body against the contract's persisted global_state.

    On approval the new global_state is saved (within the caller's transaction).
    Raises LapisExecutionError if the contract rejects.
    Raises ValueError if no contract is attached.
    """
    contract = getattr(instrument, "contract", None)
    if contract is None or not getattr(contract, "code", ""):
        raise ValueError(
            f"Instrument '{instrument.reference}' has no Lapis contract. "
            "Run sync_contract_for_instrument first."
        )

    tree = loads_contract(contract.code, fmt="yaml")
    body = LapisCompiler().compile_action(tree, action_name)

    # Seed global_state if it was never initialised (e.g. pre-existing instruments).
    if not contract.global_state:
        init_global_state(contract)

    ctx = LapisContext(
        global_state=dict(contract.global_state),
        local_state={},
        boxes={},
        transaction={},
        group_transactions=[],
        global_fields={},
        app_args=[],
    )

    LapisExecutor().execute(body, ctx)

    if ctx.result != "approve":
        raise LapisExecutionError(
            f"Lapis contract rejected action '{action_name}' for '{instrument.reference}'."
        )

    logger.debug(
        "lapis_runner.gate approved action=%s instrument=%s logs=%s",
        action_name, instrument.reference, ctx.logs,
    )

    contract.global_state = ctx.global_state
    contract.save(update_fields=["global_state"])
    return ctx


def init_global_state(contract, initial_status: str = "draft") -> None:
    """Seed the global_state of a freshly created contract."""
    contract.global_state = {"status": initial_status}
    contract.save(update_fields=["global_state"])
