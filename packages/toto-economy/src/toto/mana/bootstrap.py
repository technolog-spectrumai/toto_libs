"""The three pools every install has — minted and bound on every ingress.

Called from the end of ``toto.assets.services.bootstrap.bootstrap_economy``,
which ``IngressCommand`` runs before EVERY ingress command. That is the
"obligatory ingress at platform start": no operator step, no separate command
to remember, and cheap when there is nothing to do — a handful of existence
queries.

Two rules, both borrowed from the core-asset bootstrap beside it:

* **Mint once.** ``create_currency`` is one-shot per reference
  (``create-blue``), and that reference is checked before minting, so a
  ledger restored without its asset rows cannot receive a second opening
  supply.
* **Bind once, never overwrite.** ``ManaPool`` rows are ``get_or_create``d.
  Staff change the dials and re-point the FK; a deploy must not undo that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .colours import HUE, REGEN_DEFAULTS, ROLES, TICKER

#: The supply of each colour. A pool refills continuously out of the reserve,
#: so the number suits a slow drip for years, not a symbolic total — the same
#: reasoning MANA's supply had. Engraved into the currency hash; it can never
#: be raised, only re-engraved as a new currency.
SUPPLY = Decimal("1000000000")
DECIMALS = 9

#: The retired asset. Deactivated, never deleted: balances somebody holds are
#: ledger history, and an inactive asset still reads.
LEGACY_UNIT = "MANA"
LEGACY_FAUCET_SLUG = "default-mana"

_NAMES = {"security": "Security mana", "compute": "Compute mana",
          "storage": "Storage mana"}


@dataclass(frozen=True)
class ManaAsset:
    role: str
    unit_name: str
    name: str
    metadata: dict = field(default_factory=dict)

    @property
    def reference(self) -> str:
        return f"create-{self.unit_name.lower()}"


MANA_ASSETS: tuple[ManaAsset, ...] = tuple(
    ManaAsset(
        role=role,
        unit_name=TICKER[role],
        name=_NAMES[role],
        metadata={"kind": "mana", "family": "toto_mana", "role": role,
                  "hue": HUE[role], "plural": _NAMES[role],
                  "seeded_by": "ingress"},
    )
    for role in ROLES
)


def _say(reporter, message: str) -> None:
    if reporter is not None:
        reporter.write(message)


def retire_legacy_mana(*, reporter=None) -> int:
    """Switch off the single MANA currency and its faucet. Returns rows changed.

    Only the INGRESS-seeded one: a "MANA" somebody minted by hand is theirs,
    and the metadata marker is what tells the two apart. Idempotent — a second
    run finds nothing active to change.
    """
    from toto.assets.models import Asset, Faucet

    changed = Asset.objects.filter(
        unit_name=LEGACY_UNIT, active=True,
        metadata__seeded_by="ingress").update(active=False)
    changed += Faucet.objects.filter(
        slug=LEGACY_FAUCET_SLUG, active=True).update(active=False)
    if changed:
        _say(reporter, f"  - retired {LEGACY_UNIT} and its faucet "
                       "(superseded by the three mana pools)")
    return changed


def ensure_mana_assets(*, reporter=None) -> dict:
    """``{role: Asset}`` for whichever colours exist afterwards.

    On a host that cannot issue (a branch) that is whatever was already there,
    which may be nothing — and nothing is a legitimate answer, not an error.
    """
    from toto.assets.issuer import is_monetary_master
    from toto.assets.models import Asset, LedgerTransaction
    from toto.assets.services.bootstrap import RESERVE_NAME, ensure_currency_reserve

    present = {}
    for spec in MANA_ASSETS:
        asset = Asset.objects.filter(unit_name=spec.unit_name).first()
        if asset is not None:
            present[spec.role] = asset
            continue
        if LedgerTransaction.objects.filter(reference=spec.reference).exists():
            # Minted before, row gone — never mint a second supply.
            continue
        if not is_monetary_master():
            continue
        from toto.mint.services import create_currency

        reserve = ensure_currency_reserve()
        asset = create_currency(
            name=spec.name, unit_name=spec.unit_name, total_supply=SUPPLY,
            decimals=DECIMALS, reserve_account=reserve,
            reference=spec.reference,
            description=(f"{spec.name} — one of the three pools a member "
                         f"sees. Refills hourly up to a cap."),
            metadata=dict(spec.metadata))
        asset.reserve_account = reserve
        asset.backing_document = (
            "Issued and held by the platform's Currency Reserve account.")
        asset.minting_authority = RESERVE_NAME
        asset.save(update_fields=["reserve_account", "backing_document",
                                  "minting_authority", "updated_at"])
        present[spec.role] = asset
        _say(reporter, f"  + asset {spec.unit_name} supply={SUPPLY}")
    return present


def ensure_pools(assets: dict, *, reporter=None) -> dict:
    """Bind each role to its asset once. Never touches an existing binding."""
    from .models import ManaPool

    pools = {}
    for role, asset in assets.items():
        regen, maximum = REGEN_DEFAULTS[role]
        pool, created = ManaPool.objects.get_or_create(
            role=role,
            defaults={"asset": asset, "regen_per_hour": regen,
                      "max_pool": maximum})
        pools[role] = pool
        if created:
            _say(reporter, f"  + mana pool {role} → {asset.unit_name} "
                           f"(+{regen}/h, max {maximum})")
    return pools


def ensure_mana(*, reporter=None) -> dict:
    """Retire MANA, mint the colours, bind the pools. Never raises.

    An ingress run for some unrelated app must not die because mana could not
    be set up — the same promise ``bootstrap_economy`` makes for itself.
    """
    try:
        retire_legacy_mana(reporter=reporter)
        return ensure_pools(ensure_mana_assets(reporter=reporter),
                            reporter=reporter)
    except Exception as exc:                            # noqa: BLE001
        _say(reporter, f"  ⚠ mana bootstrap skipped: {exc}")
        return {}
