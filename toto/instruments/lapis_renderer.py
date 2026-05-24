"""
Render instrument-specific Lapis YAML contracts via Jinja2 templates.

Templates live in lapis_templates/ next to this module.
Each template is a YAML file with Jinja2 placeholders; the rendered
string is valid Lapis YAML (parseable by assets.lapis.loader).
"""
from __future__ import annotations

from pathlib import Path

import jinja2

_TEMPLATE_DIR = Path(__file__).parent / "lapis_templates"

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
    undefined=jinja2.StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


def _render(template_name: str, **ctx) -> str:
    return _env.get_template(template_name).render(**ctx)


def render_subscription(sub) -> str:
    return _render(
        "subscription.yaml.j2",
        reference=sub.instrument.reference,
        amount_base_units=sub.amount_base_units,
    )


def render_escrow(escrow) -> str:
    return _render(
        "escrow.yaml.j2",
        reference=escrow.instrument.reference,
        amount_base_units=escrow.amount_base_units,
    )


def render_forward(fwd) -> str:
    return _render(
        "forward.yaml.j2",
        reference=fwd.instrument.reference,
        payment_amount_base_units=fwd.payment_amount_base_units,
    )


def render_future(fut) -> str:
    return _render(
        "future.yaml.j2",
        reference=fut.instrument.reference,
        entry_price_base_units=fut.entry_price_base_units,
    )


def render_option(opt) -> str:
    return _render(
        "option.yaml.j2",
        reference=opt.instrument.reference,
        strike_price_base_units=opt.strike_price_base_units,
        premium_base_units=opt.premium_base_units or 0,
    )


def render_vesting(vest) -> str:
    return _render(
        "vesting.yaml.j2",
        reference=vest.instrument.reference,
        total_amount_base_units=vest.total_amount_base_units,
    )


def render_staking(staking) -> str:
    return _render(
        "staking.yaml.j2",
        reference=staking.instrument.reference,
        staked_amount_base_units=staking.staked_amount_base_units,
    )


def render_lease(lease) -> str:
    return _render(
        "lease.yaml.j2",
        reference=lease.instrument.reference,
        fixed_fee_base_units=lease.fixed_fee_base_units,
    )


def render_amortization(amort) -> str:
    return _render(
        "amortization.yaml.j2",
        reference=amort.instrument.reference,
        original_amount_base_units=amort.original_amount_base_units,
    )


def render_revenue_share(rs) -> str:
    recipients = [
        {"account_code": r.account.code, "share_bps": r.share_bps}
        for r in rs.recipients.select_related("account").all()
    ]
    return _render(
        "revenue_share.yaml.j2",
        reference=rs.instrument.reference,
        recipients=recipients,
    )


_RENDERERS = {
    "subscription": render_subscription,
    "escrow": render_escrow,
    "forward": render_forward,
    "future": render_future,
    "option": render_option,
    "vesting": render_vesting,
    "staking": render_staking,
    "lease": render_lease,
    "amortization": render_amortization,
    "revenue_share": render_revenue_share,
}


def render_for_instrument(instrument) -> str:
    """Render Lapis YAML for any supported instrument type."""
    from toto.instruments import models as _m

    _subtype_models = {
        "subscription": _m.SubscriptionContract,
        "lease": _m.LeaseContract,
        "amortization": _m.AmortizationContract,
        "vesting": _m.VestingContract,
        "escrow": _m.EscrowContract,
        "forward": _m.ForwardContract,
        "option": _m.OptionContract,
        "staking": _m.StakingPosition,
        "revenue_share": _m.RevenueShareContract,
        "future": _m.FutureContract,
    }

    renderer = _RENDERERS.get(instrument.instrument_type)
    model = _subtype_models.get(instrument.instrument_type)
    if not renderer or not model:
        raise ValueError(f"No Jinja2 template for instrument type {instrument.instrument_type!r}")

    try:
        sub = model.objects.get(instrument=instrument)
    except model.DoesNotExist:
        raise ValueError(f"No {model.__name__} found for instrument {instrument.pk}")

    return renderer(sub)
