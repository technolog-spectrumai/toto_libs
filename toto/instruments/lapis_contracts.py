"""
Generate canonical Lapis YAML contracts from financial instrument parameters.

Design rules:
- Lapis handles state-machine transitions, assertions, and payment mechanics.
- Business obligations (who owes what, when) live in Contract.metadata["obligations"].
- No oblig / transfer / record / account / asset / amount nodes are generated.
"""
from __future__ import annotations

from typing import Any

import yaml


# ── AST helpers ──────────────────────────────────────────────────────────────

def _b(v: str) -> dict:
    return {"type": "bytes", "value": v}

def _u(v: int) -> dict:
    return {"type": "uint64", "value": v}

def _get(key: str) -> dict:
    return {"type": "app_global_get", "key": _b(key)}

def _put(key: str, val: dict) -> dict:
    return {"type": "app_global_put", "key": _b(key), "value": val}

def _set_status(s: str) -> dict:
    return _put("status", _b(s))

def _assert_status(expected: str) -> dict:
    return {
        "type": "assert",
        "condition": {"type": "eq", "left": _get("status"), "right": _b(expected)},
    }

def _log(msg: str) -> dict:
    return {"type": "log", "value": _b(msg)}

def _approve() -> dict:
    return {"type": "approve"}

def _seq(steps: list[dict]) -> dict:
    return {"type": "seq", "steps": steps}

def _action(steps: list[dict]) -> dict:
    return {"body": _seq(steps)}

def _payment(amount_base_units: int) -> list[dict]:
    return [
        {"type": "inner_transaction_begin"},
        {"type": "inner_transaction_set", "field": "TypeEnum", "value": _u(1)},
        {"type": "inner_transaction_set", "field": "Amount", "value": _u(amount_base_units)},
        {"type": "inner_transaction_submit"},
    ]

def _to_yaml(tree: dict) -> str:
    return yaml.safe_dump(tree, sort_keys=False, allow_unicode=True)


# ── instrument-specific renderers ────────────────────────────────────────────

def render_subscription_lapis(sub) -> str:
    instr = sub.instrument
    tree = {
        "language": "lapis",
        "version": 1,
        "name": f"Subscription:{instr.reference}",
        "actions": {
            "activate": _action([
                _assert_status("draft"),
                _set_status("active"),
                _log("subscription_activated"),
                _approve(),
            ]),
            "bill_period": _action([
                _assert_status("active"),
                *_payment(sub.amount_base_units),
                _log("period_billed"),
                _approve(),
            ]),
            "pause": _action([
                _assert_status("active"),
                _set_status("paused"),
                _log("subscription_paused"),
                _approve(),
            ]),
            "resume": _action([
                _assert_status("paused"),
                _set_status("active"),
                _log("subscription_resumed"),
                _approve(),
            ]),
            "cancel": _action([
                _set_status("cancelled"),
                _log("subscription_cancelled"),
                _approve(),
            ]),
        },
    }
    return _to_yaml(tree)


def render_lease_lapis(lease) -> str:
    instr = lease.instrument
    tree = {
        "language": "lapis",
        "version": 1,
        "name": f"Lease:{instr.reference}",
        "actions": {
            "activate": _action([
                _assert_status("draft"),
                _set_status("active"),
                _log("lease_activated"),
                _approve(),
            ]),
            "charge": _action([
                _assert_status("active"),
                *_payment(lease.fixed_fee_base_units),
                _log("lease_charged"),
                _approve(),
            ]),
            "cancel": _action([
                _set_status("cancelled"),
                _log("lease_cancelled"),
                _approve(),
            ]),
            "expire": _action([
                _set_status("expired"),
                _log("lease_expired"),
                _approve(),
            ]),
        },
    }
    return _to_yaml(tree)


def render_amortization_lapis(amort) -> str:
    instr = amort.instrument
    tree = {
        "language": "lapis",
        "version": 1,
        "name": f"Amortization:{instr.reference}",
        "actions": {
            "activate": _action([
                _assert_status("draft"),
                _set_status("active"),
                _log("amortization_activated"),
                _approve(),
            ]),
            "amortize": _action([
                _assert_status("active"),
                *_payment(amort.original_amount_base_units),
                _log("amortization_entry"),
                _approve(),
            ]),
            "pause": _action([
                _assert_status("active"),
                _set_status("paused"),
                _log("amortization_paused"),
                _approve(),
            ]),
            "resume": _action([
                _assert_status("paused"),
                _set_status("active"),
                _log("amortization_resumed"),
                _approve(),
            ]),
            "cancel": _action([
                _set_status("cancelled"),
                _log("amortization_cancelled"),
                _approve(),
            ]),
        },
    }
    return _to_yaml(tree)


def render_vesting_lapis(vesting) -> str:
    instr = vesting.instrument
    tree = {
        "language": "lapis",
        "version": 1,
        "name": f"Vesting:{instr.reference}",
        "actions": {
            "activate": _action([
                _assert_status("draft"),
                _set_status("active"),
                _log("vesting_activated"),
                _approve(),
            ]),
            "release": _action([
                _assert_status("active"),
                *_payment(vesting.total_amount_base_units),
                _log("vesting_released"),
                _approve(),
            ]),
            "cancel": _action([
                _assert_status("active"),
                _set_status("cancelled"),
                _log("vesting_cancelled"),
                _approve(),
            ]),
        },
    }
    return _to_yaml(tree)


def render_escrow_lapis(escrow) -> str:
    instr = escrow.instrument
    tree = {
        "language": "lapis",
        "version": 1,
        "name": f"Escrow:{instr.reference}",
        "actions": {
            "fund": _action([
                _assert_status("draft"),
                *_payment(escrow.amount_base_units),
                _set_status("funded"),
                _log("escrow_funded"),
                _approve(),
            ]),
            "release": _action([
                _assert_status("funded"),
                *_payment(escrow.amount_base_units),
                _set_status("released"),
                _log("escrow_released"),
                _approve(),
            ]),
            "refund": _action([
                _assert_status("funded"),
                *_payment(escrow.amount_base_units),
                _set_status("refunded"),
                _log("escrow_refunded"),
                _approve(),
            ]),
            "dispute": _action([
                _assert_status("funded"),
                _set_status("disputed"),
                _log("escrow_disputed"),
                _approve(),
            ]),
        },
    }
    return _to_yaml(tree)


def render_forward_lapis(fwd) -> str:
    instr = fwd.instrument
    tree = {
        "language": "lapis",
        "version": 1,
        "name": f"Forward:{instr.reference}",
        "actions": {
            "activate": _action([
                _assert_status("draft"),
                _set_status("active"),
                _log("forward_activated"),
                _approve(),
            ]),
            "settle": _action([
                _assert_status("active"),
                _set_status("settled"),
                _log("forward_settled"),
                _approve(),
            ]),
            "cancel": _action([
                _assert_status("active"),
                _set_status("cancelled"),
                _log("forward_cancelled"),
                _approve(),
            ]),
        },
    }
    return _to_yaml(tree)


def render_option_lapis(option) -> str:
    instr = option.instrument
    tree = {
        "language": "lapis",
        "version": 1,
        "name": f"Option:{instr.reference}",
        "actions": {
            "activate": _action([
                _assert_status("draft"),
                _set_status("active"),
                _log("option_activated"),
                _approve(),
            ]),
            "exercise": _action([
                _assert_status("active"),
                *_payment(option.premium_base_units if option.premium_base_units else option.strike_price_base_units),
                _set_status("exercised"),
                _log("option_exercised"),
                _approve(),
            ]),
            "expire": _action([
                _assert_status("active"),
                _set_status("expired"),
                _log("option_expired"),
                _approve(),
            ]),
        },
    }
    return _to_yaml(tree)


def render_staking_lapis(staking) -> str:
    instr = staking.instrument
    tree = {
        "language": "lapis",
        "version": 1,
        "name": f"Staking:{instr.reference}",
        "actions": {
            "stake": _action([
                _assert_status("draft"),
                *_payment(staking.staked_amount_base_units),
                _set_status("staked"),
                _log("staking_staked"),
                _approve(),
            ]),
            "unstake": _action([
                _assert_status("staked"),
                *_payment(staking.staked_amount_base_units),
                _set_status("unstaked"),
                _log("staking_unstaked"),
                _approve(),
            ]),
        },
    }
    return _to_yaml(tree)


def render_revenue_share_lapis(rs) -> str:
    instr = rs.instrument
    tree = {
        "language": "lapis",
        "version": 1,
        "name": f"RevenueShare:{instr.reference}",
        "actions": {
            "activate": _action([
                _assert_status("draft"),
                _set_status("active"),
                _log("revenue_share_activated"),
                _approve(),
            ]),
            "distribute": _action([
                _assert_status("active"),
                _log("revenue_share_distributed"),
                _approve(),
            ]),
            "deactivate": _action([
                _assert_status("active"),
                _set_status("inactive"),
                _log("revenue_share_deactivated"),
                _approve(),
            ]),
        },
    }
    return _to_yaml(tree)


# ── subtype resolution ────────────────────────────────────────────────────────

_RENDERERS = {
    "subscription": render_subscription_lapis,
    "lease": render_lease_lapis,
    "amortization": render_amortization_lapis,
    "vesting": render_vesting_lapis,
    "escrow": render_escrow_lapis,
    "forward": render_forward_lapis,
    "option": render_option_lapis,
    "staking": render_staking_lapis,
    "revenue_share": render_revenue_share_lapis,
}


def _get_subtype(instrument):
    from toto.instruments import models as _m

    _model_map = {
        "subscription": _m.SubscriptionContract,
        "lease": _m.LeaseContract,
        "amortization": _m.AmortizationContract,
        "vesting": _m.VestingContract,
        "escrow": _m.EscrowContract,
        "forward": _m.ForwardContract,
        "option": _m.OptionContract,
        "staking": _m.StakingPosition,
        "revenue_share": _m.RevenueShareContract,
    }
    model = _model_map.get(instrument.instrument_type)
    if not model:
        return None
    try:
        return model.objects.get(instrument=instrument)
    except model.DoesNotExist:
        return None


# ── obligation memory ─────────────────────────────────────────────────────────

def build_obligation_memory_for_instrument(instrument) -> list[dict]:
    t = instrument.instrument_type
    sub = _get_subtype(instrument)
    if not sub:
        return []

    if t == "subscription":
        return [{
            "role": "recurring_payment",
            "debtor_account_id": sub.subscriber_account_id,
            "creditor_account_id": sub.provider_account_id,
            "asset_id": sub.asset_id,
            "amount_base_units": sub.amount_base_units,
            "billing_cycle": sub.billing_cycle,
            "note": f"Subscriber owes {sub.amount_base_units} base units per {sub.billing_cycle}.",
        }]

    if t == "lease":
        return [{
            "role": "recurring_payment",
            "debtor_account_id": sub.lessee_account_id,
            "creditor_account_id": sub.revenue_account_id,
            "asset_id": sub.payment_asset_id,
            "amount_base_units": sub.fixed_fee_base_units,
            "billing_period": sub.billing_period,
            "note": f"Lessee owes {sub.fixed_fee_base_units} base units per {sub.billing_period}.",
        }]

    if t == "amortization":
        return [{
            "role": "amortization_payment",
            "debtor_account_id": sub.source_account_id,
            "creditor_account_id": sub.destination_account_id,
            "asset_id": sub.asset_id,
            "amount_base_units": sub.original_amount_base_units,
            "note": f"Source owes {sub.original_amount_base_units} base units total across amortization schedule.",
        }]

    if t == "vesting":
        return [{
            "role": "vesting_release",
            "debtor_account_id": sub.grantor_account_id,
            "creditor_account_id": sub.beneficiary_account_id,
            "asset_id": sub.asset_id,
            "amount_base_units": sub.total_amount_base_units,
            "note": f"Grantor owes {sub.total_amount_base_units} base units to beneficiary per vesting schedule.",
        }]

    if t == "escrow":
        return [
            {
                "role": "escrow_release",
                "debtor_account_id": sub.escrow_account_id,
                "creditor_account_id": sub.seller_account_id,
                "asset_id": sub.asset_id,
                "amount_base_units": sub.amount_base_units,
                "note": "Escrow account owes asset to seller on release.",
            },
            {
                "role": "escrow_refund",
                "debtor_account_id": sub.escrow_account_id,
                "creditor_account_id": sub.buyer_account_id,
                "asset_id": sub.asset_id,
                "amount_base_units": sub.amount_base_units,
                "note": "Escrow account owes asset to buyer on refund.",
            },
        ]

    if t == "forward":
        return [
            {
                "role": "underlying_delivery",
                "debtor_account_id": sub.seller_account_id,
                "creditor_account_id": sub.buyer_account_id,
                "asset_id": sub.underlying_asset_id,
                "amount_base_units": sub.quantity_base_units,
                "due_at": sub.settlement_at.isoformat(),
                "note": "Seller owes underlying asset to buyer at settlement.",
            },
            {
                "role": "payment",
                "debtor_account_id": sub.buyer_account_id,
                "creditor_account_id": sub.seller_account_id,
                "asset_id": sub.payment_asset_id,
                "amount_base_units": sub.payment_amount_base_units,
                "due_at": sub.settlement_at.isoformat(),
                "note": "Buyer owes payment asset to seller at settlement.",
            },
        ]

    if t == "option":
        obligations = [{
            "role": "premium",
            "debtor_account_id": sub.buyer_account_id,
            "creditor_account_id": sub.writer_account_id,
            "asset_id": sub.payment_asset_id,
            "amount_base_units": sub.premium_base_units,
            "due_at": sub.expiry_at.isoformat(),
            "note": "Buyer owes premium to writer.",
        }]
        if sub.premium_base_units:
            obligations.append({
                "role": "payout",
                "debtor_account_id": sub.writer_account_id,
                "creditor_account_id": sub.buyer_account_id,
                "asset_id": sub.underlying_asset_id,
                "amount_base_units": sub.quantity_base_units,
                "due_at": sub.expiry_at.isoformat(),
                "note": "Writer owes underlying (or cash equivalent) to buyer if exercised.",
            })
        return obligations

    if t == "staking":
        return [
            {
                "role": "stake_deposit",
                "debtor_account_id": sub.staker_account_id,
                "creditor_account_id": sub.staking_account_id,
                "asset_id": sub.staked_asset_id,
                "amount_base_units": sub.staked_amount_base_units,
                "note": "Staker deposits staked asset to staking account.",
            },
            {
                "role": "reward",
                "debtor_account_id": sub.staking_account_id,
                "creditor_account_id": sub.staker_account_id,
                "asset_id": sub.reward_asset_id,
                "reward_rate_bps": sub.reward_rate_bps,
                "note": f"Staking account owes rewards at {sub.reward_rate_bps} bps.",
            },
        ]

    if t == "revenue_share":
        recipients = list(sub.recipients.all())
        return [
            {
                "role": "distribution",
                "creditor_account_id": r.account_id,
                "debtor_account_id": sub.revenue_account_id,
                "asset_id": sub.revenue_asset_id,
                "share_bps": r.share_bps,
                "note": f"Revenue account distributes {r.share_bps} bps to recipient.",
            }
            for r in recipients
        ]

    return []


# ── metadata builder ──────────────────────────────────────────────────────────

def build_contract_metadata_for_instrument(instrument) -> dict:
    return {
        "instrument_id": instrument.pk,
        "instrument_reference": instrument.reference,
        "instrument_type": instrument.instrument_type,
        "generated_by": "instruments.lapis_contracts",
        "lapis_version": 1,
        "obligations": build_obligation_memory_for_instrument(instrument),
    }


# ── YAML dispatcher ───────────────────────────────────────────────────────────

def render_lapis_for_instrument(instrument) -> str:
    sub = _get_subtype(instrument)
    renderer = _RENDERERS.get(instrument.instrument_type)
    if not sub or not renderer:
        raise ValueError(
            f"Cannot render Lapis for {instrument.instrument_type!r}: "
            f"subtype record missing or unsupported."
        )
    return renderer(sub)


# ── sync ──────────────────────────────────────────────────────────────────────

def sync_contract_for_instrument(instrument):
    """Create or update the assets.Contract linked to this instrument.

    Generates canonical Lapis YAML, validates it, and persists the Contract.
    Does not touch ledger balances.
    """
    from django.db import transaction as _tx
    from toto.assets.models import Contract
    from toto.assets.lapis.loader import loads_contract
    from toto.assets.lapis.compiler import LapisCompiler
    from toto.assets.lapis.exceptions import LapisValidationError

    code = render_lapis_for_instrument(instrument)
    tree = loads_contract(code, fmt="yaml")
    try:
        LapisCompiler().validate_contract(tree)
    except LapisValidationError as exc:
        raise ValueError(f"Generated Lapis failed validation: {exc}") from exc

    metadata = build_contract_metadata_for_instrument(instrument)
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


def build_contract_for_instrument(instrument):
    """Create and link a Contract for the instrument (no-op if already linked)."""
    if instrument.contract_id:
        return instrument.contract
    return sync_contract_for_instrument(instrument)
