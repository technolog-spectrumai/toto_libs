"""What this platform bills in.

The single answer to "which asset do we charge in", replacing a per-host
``GAS_ASSET`` ticker. A ticker is a label and labels drift; a contract names an
asset by its genesis hash and says who granted it.

The master self-contracts, so there is exactly one code path here and the
branch path is exercised on the only host that has a ledger today.
"""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

log = logging.getLogger("toto.assets.contracts")


def node_id() -> str:
    """This platform's stable name. Whatever the deploy calls the host."""
    from django.conf import settings

    return (getattr(settings, "CLEARING_SELF_PLATFORM_ID", "")
            or getattr(settings, "PLATFORM_DOMAIN", "")
            or "unknown")


def local_contract():
    """The active contract for this platform, or None."""
    from .models import CurrencyContract

    return (CurrencyContract.objects
            .filter(is_local=True, superseded_at__isnull=True)
            .select_related("asset", "issuer").first())


def contractual_asset():
    """The asset this platform bills in, or None if it has no contract.

    ``None`` means "not contracted", which is NOT the same as "free" — the
    caller decides. The billing façade keeps its own vocabulary; nothing here
    reaches into it.
    """
    contract = local_contract()
    return contract.asset if contract else None


def contracted_assets() -> list:
    """Every asset this platform has ever been contracted for.

    Current plus superseded — the set of assets it may legitimately hold.
    Balances in a retired currency stay spendable; nothing new is priced in
    them, because pricing reads ``contractual_asset()``.
    """
    from .models import CurrencyContract

    return list({c.asset for c in CurrencyContract.objects
                 .filter(is_local=True).select_related("asset")})


def may_hold(asset) -> bool:
    """Is this platform allowed to carry a balance in that asset?"""
    return any(a.pk == asset.pk for a in contracted_assets())


def build_contract_descriptor(*, node: str, asset, serial: int) -> dict:
    """What the master signs and an operator carries to a branch.

    Carries the asset's whole genesis document, so importing a contract also
    mirrors its asset. That is why there is no separate catalogue: a branch
    holds exactly one asset, and this is how it arrives.
    """
    return {
        "v": 1,
        "node_id": node,
        "serial": serial,
        "currency_hash": asset.currency_hash,
        "genesis": asset.genesis_payload,
        "genesis_signature": asset.genesis_signature,
        "issuer_public_key_pem": asset.issuer.public_key_pem,
        "issued_at": timezone.now().isoformat(),
    }


def assign_contract(*, node: str, asset, serial: int | None = None):
    """Master side: grant a platform the currency it bills in."""
    from .issuer import require_master
    from .models import CurrencyContract

    require_master("assign a currency contract")
    if not asset.currency_hash:
        raise ValidationError(
            f"“{asset.unit_name}” has no genesis hash, so it cannot be any "
            "platform's currency.")

    with transaction.atomic():
        previous = (CurrencyContract.objects.filter(node_id=node)
                    .order_by("-serial").first())
        next_serial = serial if serial is not None else (
            (previous.serial + 1) if previous else 1)
        if previous and next_serial <= previous.serial:
            raise ValidationError(
                f"Serial {next_serial} is not newer than the contract "
                f"{node} already has ({previous.serial}).")

        CurrencyContract.objects.filter(
            node_id=node, superseded_at__isnull=True).update(
                superseded_at=timezone.now())

        return CurrencyContract.objects.create(
            node_id=node, asset=asset, currency_hash=asset.currency_hash,
            issuer=asset.issuer, serial=next_serial,
            payload=build_contract_descriptor(node=node, asset=asset,
                                              serial=next_serial),
            signature=asset.issuer.sign_genesis(asset.genesis_payload),
            is_local=(node == node_id()))


def import_contract(descriptor: dict):
    """Branch side: accept the currency the master assigned, and mirror it.

    Everything consequential happens here, so everything is checked here: the
    serial must be newer, the descriptor must name THIS platform, and the
    asset's genesis signature must verify against the issuer key the
    descriptor carries. Nothing is written until all three hold.

    The asset arrives with the contract rather than from a catalogue, because
    a branch holds exactly one — see portal/hierarchical_economy.md.
    """
    from .issuer import fingerprint_for, is_monetary_master
    from .models import CurrencyContract, CurrencyIssuer
    from .services.assets import mirror_asset

    if is_monetary_master():
        raise ValidationError(
            "This platform issues its own currency; it does not accept a "
            "contract from anyone.")

    node = node_id()
    if descriptor.get("node_id") != node:
        raise ValidationError(
            f"This contract is for “{descriptor.get('node_id')}”, and this "
            f"platform is “{node}”.")

    serial = int(descriptor.get("serial", 0))
    previous = (CurrencyContract.objects.filter(node_id=node)
                .order_by("-serial").first())
    if previous and serial <= previous.serial:
        raise ValidationError(
            f"Contract serial {serial} is not newer than the one already "
            f"held ({previous.serial}). Refusing to replay an old contract.")

    public_pem = descriptor["issuer_public_key_pem"]
    with transaction.atomic():
        issuer, _ = CurrencyIssuer.objects.get_or_create(
            fingerprint=fingerprint_for(public_pem),
            defaults={"label": descriptor.get("node_id", "master"),
                      "public_key_pem": public_pem, "is_self": False})

        asset = mirror_asset(
            genesis_payload=descriptor["genesis"],
            signature=descriptor["genesis_signature"],
            issuer=issuer,
            origin_platform=descriptor.get("origin_platform", ""))

        CurrencyContract.objects.filter(
            node_id=node, superseded_at__isnull=True).update(
                superseded_at=timezone.now())

        return CurrencyContract.objects.create(
            node_id=node, asset=asset, currency_hash=asset.currency_hash,
            issuer=issuer, serial=serial, payload=descriptor,
            signature=descriptor["genesis_signature"], is_local=True)
