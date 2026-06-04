"""
High-level deploy API for financial instrument Lapis contracts.

Wraps the lower-level lapis_contracts helpers with:
  - a force= flag (no-op when contract already exists and force=False)
  - ingress metadata markers on deployed contracts
"""
from __future__ import annotations


def deploy_contract_for_instrument(instrument, *, force: bool = False):
    """
    Create (or, when force=True, update) the assets.Contract for *instrument*.

    Adds ingress metadata keys so the contract is identifiable as demo-deployed.

    force=False — no-op if instrument already has a Contract; returns it as-is.
    force=True  — regenerate YAML, validate, and overwrite code + metadata.
    """
    from django.db import transaction as _tx

    from toto.assets.lapis.compiler import ContractFlowValidator
    from toto.assets.lapis.exceptions import LapisValidationError
    from toto.assets.lapis.loader import loads_contract
    from toto.assets.models import Contract
    from toto.instruments.lapis_contracts import (
        build_contract_metadata_for_instrument,
        render_lapis_for_instrument,
    )

    if instrument.contract_id and not force:
        return instrument.contract

    code = render_lapis_for_instrument(instrument)
    tree = loads_contract(code, fmt="yaml")
    try:
        ContractFlowValidator().validate(tree)
    except LapisValidationError as exc:
        raise ValueError(f"Generated Contract Flow failed validation: {exc}") from exc

    metadata = build_contract_metadata_for_instrument(instrument)
    metadata["ingress_deployed_contract"] = True
    metadata["ingress_source"] = "ingress_instruments"

    name = f"{instrument.get_instrument_type_display()}:{instrument.reference}"

    with _tx.atomic():
        if instrument.contract_id:
            contract = instrument.contract
            contract.name = name
            contract.code = code
            contract.metadata = metadata
            contract.save(update_fields=["name", "code", "metadata"])
        else:
            contract = Contract.objects.create(name=name, code=code, metadata=metadata)
            instrument.contract = contract
            instrument.save(update_fields=["contract"])

    return contract


def sync_contract_for_instrument(instrument, *, force: bool = False):
    """
    Thin wrapper around lapis_contracts.sync_contract_for_instrument with force= support.

    force=False — no-op if instrument already has a Contract.
    force=True  — always regenerate and persist (no ingress metadata markers).
    """
    from toto.instruments.lapis_contracts import (
        sync_contract_for_instrument as _sync,
    )

    if instrument.contract_id and not force:
        return instrument.contract
    return _sync(instrument)
