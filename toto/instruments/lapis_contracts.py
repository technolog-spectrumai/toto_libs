"""
Generate Contract Flow YAML documents from financial instrument parameters.

Contract Flow documents are human-readable documentation and visualization only.
Django services remain the real enforcement layer for business logic.
"""
from __future__ import annotations

import yaml


def _flow(name: str, parties: list, assets: list, steps: list) -> dict:
    return {
        "language": "lapis",
        "version": 1,
        "kind": "contract_flow",
        "name": name,
        "parties": parties,
        "assets": assets,
        "steps": steps,
    }


def _party(id: str, label: str, **kw) -> dict:
    return {"id": id, "label": label, **kw}


def _asset(id: str, label: str, **kw) -> dict:
    return {"id": id, "label": label, **kw}


def _step(id: str, type: str, title: str, **kw) -> dict:
    return {"id": id, "type": type, "title": title, **kw}


def _to_yaml(tree: dict) -> str:
    return yaml.safe_dump(tree, sort_keys=False, allow_unicode=True)


# ── instrument-specific renderers ────────────────────────────────────────────

def render_lease_lapis(lease) -> str:
    instr = lease.instrument
    payment_label = lease.payment_asset.unit_name if lease.payment_asset_id else "Payment Asset"
    tree = _flow(
        name=f"Lease:{instr.reference}",
        parties=[
            _party("lessee", "Lessee"),
            _party("lessor", "Lessor"),
        ],
        assets=[_asset("payment", payment_label)],
        steps=[
            _step("draft", "start", "Lease created in draft status", next="activate"),
            _step("activate", "condition", "Status is draft?",
                  then="set_active", **{"else": "stop_invalid"}),
            _step("set_active", "state", "Set status = active", next="stop_activated"),
            _step("stop_activated", "stop", "Lease is now active"),
            _step("stop_invalid", "stop", "Cannot activate — lease not in draft"),
            _step("charge_trigger", "start", f"Fixed fee charge ({lease.billing_period})", next="check_active"),
            _step("check_active", "condition", "Lease is active?",
                  then="make_payment", **{"else": "stop_not_chargeable"}),
            _step("make_payment", "payment", f"Charge fixed fee ({lease.fixed_fee_base_units} base units)",
                  **{"from": "lessee", "to": "lessor", "asset": "payment",
                     "amount_base_units": lease.fixed_fee_base_units, "next": "record_charge"}),
            _step("record_charge", "record", "Log charge event", next="stop_charged"),
            _step("stop_charged", "stop", "Fixed fee charged"),
            _step("stop_not_chargeable", "stop", "Cannot charge — lease not active"),
            _step("cancel_trigger", "start", "Cancellation requested", next="set_cancelled"),
            _step("set_cancelled", "state", "Set status = cancelled", next="stop_cancelled"),
            _step("stop_cancelled", "stop", "Lease cancelled"),
        ],
    )
    return _to_yaml(tree)


def render_amortization_lapis(amort) -> str:
    instr = amort.instrument
    asset_label = amort.asset.unit_name if amort.asset_id else "Asset"
    tree = _flow(
        name=f"Amortization:{instr.reference}",
        parties=[
            _party("debtor", "Debtor", account=amort.source_account_id),
            _party("creditor", "Creditor", account=amort.destination_account_id),
        ],
        assets=[_asset("principal", asset_label)],
        steps=[
            _step("draft", "start", "Amortization created in draft", next="activate"),
            _step("activate", "condition", "Status is draft?",
                  then="set_active", **{"else": "stop_invalid"}),
            _step("set_active", "state", "Set status = active", next="stop_activated"),
            _step("stop_activated", "stop", "Amortization schedule active"),
            _step("stop_invalid", "stop", "Cannot activate — not in draft"),
            _step("amortize_trigger", "start", "Amortization instalment due", next="check_active"),
            _step("check_active", "condition", "Schedule is active?",
                  then="make_payment", **{"else": "stop_blocked"}),
            _step("make_payment", "payment", f"Transfer instalment ({amort.original_amount_base_units} base units)",
                  **{"from": "debtor", "to": "creditor", "asset": "principal",
                     "amount_base_units": amort.original_amount_base_units, "next": "record_entry"}),
            _step("record_entry", "record", "Record amortization entry", next="stop_done"),
            _step("stop_done", "stop", "Instalment recorded"),
            _step("stop_blocked", "stop", "Cannot amortize — schedule not active"),
        ],
    )
    return _to_yaml(tree)


def render_vesting_lapis(vesting) -> str:
    instr = vesting.instrument
    asset_label = vesting.asset.unit_name if vesting.asset_id else "Asset"
    tree = _flow(
        name=f"Vesting:{instr.reference}",
        parties=[
            _party("grantor", "Grantor"),
            _party("beneficiary", "Beneficiary"),
        ],
        assets=[_asset("vested", asset_label)],
        steps=[
            _step("draft", "start", "Vesting schedule created", next="activate"),
            _step("activate", "condition", "Status is draft?",
                  then="set_active", **{"else": "stop_invalid"}),
            _step("set_active", "state", "Set status = active", next="stop_activated"),
            _step("stop_activated", "stop", "Vesting schedule active"),
            _step("stop_invalid", "stop", "Cannot activate — not in draft"),
            _step("release_trigger", "start", "Vesting cliff or tranche reached", next="check_active"),
            _step("check_active", "condition", "Vesting schedule is active?",
                  then="make_payment", **{"else": "stop_blocked"}),
            _step("make_payment", "payment", f"Release vested tokens ({vesting.total_amount_base_units} base units total)",
                  **{"from": "grantor", "to": "beneficiary", "asset": "vested",
                     "amount_base_units": vesting.total_amount_base_units, "next": "record_release"}),
            _step("record_release", "record", "Record vesting release", next="stop_released"),
            _step("stop_released", "stop", "Tokens released to beneficiary"),
            _step("stop_blocked", "stop", "Cannot release — schedule not active"),
        ],
    )
    return _to_yaml(tree)


def render_escrow_lapis(escrow) -> str:
    instr = escrow.instrument
    asset_label = escrow.asset.unit_name if escrow.asset_id else "Asset"
    tree = _flow(
        name=f"Escrow:{instr.reference}",
        parties=[
            _party("buyer", "Buyer"),
            _party("seller", "Seller"),
            _party("escrow_agent", "Escrow Agent"),
        ],
        assets=[_asset("escrowed", asset_label)],
        steps=[
            _step("draft", "start", "Escrow created — awaiting funding", next="fund"),
            _step("fund", "payment", f"Buyer deposits funds ({escrow.amount_base_units} base units)",
                  **{"from": "buyer", "to": "escrow_agent", "asset": "escrowed",
                     "amount_base_units": escrow.amount_base_units, "next": "set_funded"}),
            _step("set_funded", "state", "Set status = funded", next="check_outcome"),
            _step("check_outcome", "condition", "Conditions met for release?",
                  then="release", **{"else": "check_refund"}),
            _step("check_refund", "condition", "Refund requested?",
                  then="refund", **{"else": "dispute"}),
            _step("release", "payment", "Release funds to seller",
                  **{"from": "escrow_agent", "to": "seller", "asset": "escrowed",
                     "amount_base_units": escrow.amount_base_units, "next": "set_released"}),
            _step("set_released", "state", "Set status = released", next="stop_complete"),
            _step("refund", "payment", "Refund funds to buyer",
                  **{"from": "escrow_agent", "to": "buyer", "asset": "escrowed",
                     "amount_base_units": escrow.amount_base_units, "next": "set_refunded"}),
            _step("set_refunded", "state", "Set status = refunded", next="stop_complete"),
            _step("dispute", "state", "Set status = disputed — escalate to arbitration", next="stop_disputed"),
            _step("stop_complete", "stop", "Escrow settled"),
            _step("stop_disputed", "stop", "Escrow in dispute"),
        ],
    )
    return _to_yaml(tree)


def render_forward_lapis(fwd) -> str:
    instr = fwd.instrument
    underlying_label = fwd.underlying_asset.unit_name if fwd.underlying_asset_id else "Underlying"
    payment_label = fwd.payment_asset.unit_name if fwd.payment_asset_id else "Payment"
    tree = _flow(
        name=f"Forward:{instr.reference}",
        parties=[
            _party("buyer", "Buyer"),
            _party("seller", "Seller"),
        ],
        assets=[
            _asset("underlying", underlying_label),
            _asset("payment", payment_label),
        ],
        steps=[
            _step("draft", "start", "Forward contract created", next="activate"),
            _step("activate", "condition", "Status is draft?",
                  then="set_active", **{"else": "stop_invalid"}),
            _step("set_active", "state", "Set status = active", next="stop_activated"),
            _step("stop_activated", "stop", "Forward contract active"),
            _step("stop_invalid", "stop", "Cannot activate — not in draft"),
            _step("settlement_due", "start", f"Settlement date reached ({fwd.settlement_at.date()})", next="settle_payment"),
            _step("settle_payment", "payment", f"Buyer pays {fwd.payment_amount_base_units} base units",
                  **{"from": "buyer", "to": "seller", "asset": "payment",
                     "amount_base_units": fwd.payment_amount_base_units, "next": "settle_delivery"}),
            _step("settle_delivery", "payment", f"Seller delivers {fwd.quantity_base_units} base units",
                  **{"from": "seller", "to": "buyer", "asset": "underlying",
                     "amount_base_units": fwd.quantity_base_units, "next": "set_settled"}),
            _step("set_settled", "state", "Set status = settled", next="stop_settled"),
            _step("stop_settled", "stop", "Forward contract settled"),
        ],
    )
    return _to_yaml(tree)


def render_option_lapis(option) -> str:
    instr = option.instrument
    payment_label = option.payment_asset.unit_name if option.payment_asset_id else "Payment"
    underlying_label = option.underlying_asset.unit_name if option.underlying_asset_id else "Underlying"
    tree = _flow(
        name=f"Option:{instr.reference}",
        parties=[
            _party("buyer", "Option Buyer"),
            _party("writer", "Option Writer"),
        ],
        assets=[
            _asset("premium", payment_label),
            _asset("underlying", underlying_label),
        ],
        steps=[
            _step("draft", "start", "Option contract created", next="activate"),
            _step("activate", "condition", "Status is draft?",
                  then="set_active", **{"else": "stop_invalid"}),
            _step("set_active", "state", "Set status = active", next="stop_activated"),
            _step("stop_activated", "stop", "Option contract active"),
            _step("stop_invalid", "stop", "Cannot activate — not in draft"),
            _step("exercise_trigger", "start", "Buyer elects to exercise option", next="check_expiry"),
            _step("check_expiry", "condition", "Option is still active and not expired?",
                  then="pay_premium", **{"else": "stop_expired"}),
            _step("pay_premium", "payment", f"Buyer pays premium ({option.premium_base_units} base units)",
                  **{"from": "buyer", "to": "writer", "asset": "premium",
                     "amount_base_units": option.premium_base_units or 0, "next": "deliver_underlying"}),
            _step("deliver_underlying", "payment", f"Writer delivers underlying ({option.quantity_base_units} base units)",
                  **{"from": "writer", "to": "buyer", "asset": "underlying",
                     "amount_base_units": option.quantity_base_units, "next": "set_exercised"}),
            _step("set_exercised", "state", "Set status = exercised", next="stop_exercised"),
            _step("stop_exercised", "stop", "Option exercised"),
            _step("stop_expired", "stop", "Option expired unexercised"),
        ],
    )
    return _to_yaml(tree)


def render_staking_lapis(staking) -> str:
    instr = staking.instrument
    staked_label = staking.staked_asset.unit_name if staking.staked_asset_id else "Staked Asset"
    reward_label = staking.reward_asset.unit_name if staking.reward_asset_id else "Reward Asset"
    tree = _flow(
        name=f"Staking:{instr.reference}",
        parties=[
            _party("staker", "Staker"),
            _party("protocol", "Staking Protocol"),
        ],
        assets=[
            _asset("staked", staked_label),
            _asset("reward", reward_label),
        ],
        steps=[
            _step("stake_trigger", "start", "Staker initiates staking", next="deposit"),
            _step("deposit", "payment", f"Deposit {staking.staked_amount_base_units} base units into protocol",
                  **{"from": "staker", "to": "protocol", "asset": "staked",
                     "amount_base_units": staking.staked_amount_base_units, "next": "set_staked"}),
            _step("set_staked", "state", "Set status = staked", next="stop_staked"),
            _step("stop_staked", "stop", "Assets staked — earning rewards"),
            _step("unstake_trigger", "start", "Staker requests unstaking", next="check_staked"),
            _step("check_staked", "condition", "Status is staked?",
                  then="return_stake", **{"else": "stop_not_staked"}),
            _step("return_stake", "payment", "Return staked assets to staker",
                  **{"from": "protocol", "to": "staker", "asset": "staked",
                     "amount_base_units": staking.staked_amount_base_units, "next": "set_unstaked"}),
            _step("set_unstaked", "state", "Set status = unstaked", next="stop_unstaked"),
            _step("stop_unstaked", "stop", "Assets unstaked"),
            _step("stop_not_staked", "stop", "Cannot unstake — assets not staked"),
        ],
    )
    return _to_yaml(tree)


def render_revenue_share_lapis(rs) -> str:
    instr = rs.instrument
    asset_label = rs.revenue_asset.unit_name if rs.revenue_asset_id else "Revenue Asset"
    recipients = list(rs.recipients.all())
    dist_steps = []
    for i, r in enumerate(recipients):
        pct = r.share_bps / 100
        next_id = f"recipient_{i + 1}" if i + 1 < len(recipients) else "record_distribution"
        dist_steps.append(
            _step(f"recipient_{i}", "payment", f"Distribute {pct:.2f}% to recipient",
                  **{"from": "revenue_account", "to": f"recipient_{i}",
                     "asset": "revenue", "share_bps": r.share_bps, "next": next_id})
        )

    if not dist_steps:
        dist_steps = [_step("no_recipients", "stop", "No recipients configured — add recipients first")]

    tree = _flow(
        name=f"RevenueShare:{instr.reference}",
        parties=[
            _party("revenue_account", "Revenue Account"),
            *[_party(f"recipient_{i}", f"Recipient {i + 1}") for i in range(len(recipients))],
        ],
        assets=[_asset("revenue", asset_label)],
        steps=[
            _step("activate_trigger", "start", "Revenue share activated", next="check_draft"),
            _step("check_draft", "condition", "Status is draft?",
                  then="set_active", **{"else": "stop_invalid"}),
            _step("set_active", "state", "Set status = active", next="stop_activated"),
            _step("stop_activated", "stop", "Revenue share active"),
            _step("stop_invalid", "stop", "Cannot activate — not in draft"),
            _step("distribute_trigger", "start", "Distribution event triggered", next="check_active"),
            _step("check_active", "condition", "Revenue share is active?",
                  then=dist_steps[0]["id"] if dist_steps else "stop_inactive",
                  **{"else": "stop_inactive"}),
            *dist_steps,
            _step("record_distribution", "record", "Log distribution event", next="stop_distributed"),
            _step("stop_distributed", "stop", "Distribution complete"),
            _step("stop_inactive", "stop", "Cannot distribute — not active"),
        ],
    )
    return _to_yaml(tree)


# ── subtype resolution ────────────────────────────────────────────────────────

_RENDERERS = {
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


# ── obligation metadata ───────────────────────────────────────────────────────

def build_obligation_memory_for_instrument(instrument) -> list[dict]:
    t = instrument.instrument_type
    sub = _get_subtype(instrument)
    if not sub:
        return []

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
            "note": f"Source owes {sub.original_amount_base_units} base units total.",
        }]

    if t == "vesting":
        return [{
            "role": "vesting_release",
            "debtor_account_id": sub.grantor_account_id,
            "creditor_account_id": sub.beneficiary_account_id,
            "asset_id": sub.asset_id,
            "amount_base_units": sub.total_amount_base_units,
            "note": f"Grantor owes {sub.total_amount_base_units} base units per vesting schedule.",
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
            f"Cannot render Contract Flow for {instrument.instrument_type!r}: "
            f"subtype record missing or unsupported."
        )
    return renderer(sub)


# ── sync ──────────────────────────────────────────────────────────────────────

def sync_contract_for_instrument(instrument):
    """Create or update the assets.Contract linked to this instrument.

    Generates Contract Flow YAML and persists the Contract.
    Does not touch ledger balances.
    """
    from django.db import transaction as _tx
    from toto.assets.models import Contract
    from toto.assets.lapis.loader import loads_contract
    from toto.assets.lapis.compiler import ContractFlowValidator
    from toto.assets.lapis.exceptions import LapisValidationError

    code = render_lapis_for_instrument(instrument)
    tree = loads_contract(code, fmt="yaml")
    try:
        ContractFlowValidator().validate(tree)
    except LapisValidationError as exc:
        raise ValueError(f"Generated Contract Flow failed validation: {exc}") from exc

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
