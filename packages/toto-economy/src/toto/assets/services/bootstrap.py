"""What every Ingress run guarantees about the economy.

One service, called from :class:`toto.ingress.IngressCommand` so that EVERY
bootstrap path reaches it and not only ``ingress_all``. It is cheap when there
is nothing to do — a handful of existence queries — and it is idempotent by
construction, so running any ingress command twice changes nothing.

## Why the issuer is minted here

A currency is engraved by the monetary master, and a host is the master when it
holds an issuer keypair. ``mint_issuer_key`` is a management command with a
warning attached — *"deliberately a management command and never a migration: a
migration runs on every host that migrates, so minting there would manufacture a
monetary master on each one"* — and that warning is right about migrations and
silent about this.

The gap it left is a dead end an operator cannot get out of without knowing the
command exists:

* ``deploy.py`` mints ``MONETARY_ISSUER_KEY`` into every host's environment, so
  the SECRET is there;
* nothing ever creates the issuer ROW, so ``is_monetary_master()`` is False;
* ``ingress_assets`` therefore seeds no currency and says so politely;
* with no currencies, the Assets UI answers 500 on every mint (``NotTheMaster``
  escaping a narrow ``except``) and no tariff price can be saved, because the
  charged-asset field has nothing valid to point at.

Ingress is an operator act, which is the bar the warning actually sets. So the
issuer is minted here, once, guarded by :data:`MASTER_SETTING` for the host that
genuinely must not have one.

## Why supplies are constants and not arguments

A maximum supply is engraved into the currency hash and can never be raised —
"needing more means engraving a new currency". So the number lives here, in one
place, as a named constant, rather than being computed or passed in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings

#: Set to False on a host that must NEVER issue: a branch mirrors what the
#: master engraved and imports it as a signed contract. Deploy hands every host
#: an issuer secret, so the secret alone cannot decide this — the operator does.
MASTER_SETTING = "ASSETS_MONETARY_MASTER"

#: The ledger account every core currency is minted into.
RESERVE_CODE = "currency-reserve"
RESERVE_NAME = "Currency Reserve"


@dataclass(frozen=True)
class CoreAsset:
    """One currency this platform is not itself without."""

    unit_name: str
    name: str
    decimals: int
    supply: Decimal
    description: str
    metadata: dict = field(default_factory=dict)

    @property
    def reference(self) -> str:
        """The mint's idempotency key.

        ``create_currency`` is one-shot per reference and that IS the
        immutability guarantee for a fixed supply, so re-running ingress can
        never mint a second opening supply.
        """
        return f"create-{self.unit_name.lower()}"


#: The three currencies every install has, in the order they are created.
#:
#: ASR and TPLN carry the supplies they were first seeded with, deliberately
#: frozen: re-running ingress against an already-seeded ledger must not trip the
#: immutability re-check. MANA is new in 8/2026 — it is the default settlement
#: asset (see toto.assets.services.settlement), so it is the one that pays
#: faucets by the hour and needs a supply that suits a slow continuous drip
#: rather than a symbolic total.
CORE_ASSETS: tuple[CoreAsset, ...] = (
    CoreAsset(
        unit_name="MANA", name="Mana", decimals=9,
        supply=Decimal("1000000000"),
        description=("Mana — the platform's settlement asset. What internal "
                     "payments, rewards and faucets are paid in. 9 decimal "
                     "places."),
        metadata={"kind": "currency", "family": "toto_currency",
                  "plural": "Mana", "seeded_by": "ingress"},
    ),
    CoreAsset(
        unit_name="ASR", name="Assarion", decimals=9,
        supply=Decimal("6666.666666667"),
        description=("Assarion — fine-grained unit of account of the platform. "
                     "9 decimal places."),
        metadata={"kind": "currency", "family": "toto_currency",
                  "plural": "Assari", "seeded_by": "ingress"},
    ),
    CoreAsset(
        unit_name="TPLN", name="Toto Złoty", decimals=2,
        supply=Decimal("76658.70"),
        description=("Internal accounting currency of the platform. "
                     "2 decimal places."),
        metadata={"kind": "currency", "family": "toto_currency",
                  "seeded_by": "ingress"},
    ),
)

#: Frozen on purpose: these two reached live ledgers at these numbers, and a
#: supply that moved would be a supply somebody has to reconcile by hand.
assert CORE_ASSETS[1].supply == Decimal("6666.666666667")
assert CORE_ASSETS[2].supply == Decimal("76658.70")


def _say(reporter, message: str) -> None:
    if reporter is not None:
        reporter.write(message)


# --------------------------------------------------------------------------- #
# The issuer                                                                   #
# --------------------------------------------------------------------------- #

def ensure_monetary_issuer(*, label: str = "", reporter=None):
    """This host's issuer keypair, minted once if it may have one.

    Returns the issuer, or None when this host is not the master or cannot open
    an issuer key. Never raises: a host that cannot issue is a legitimate host,
    and every caller of this already has a "no currency here" path.
    """
    from toto.assets.issuer import local_issuer, mint_issuer

    existing = local_issuer()
    if existing is not None:
        return existing

    if not getattr(settings, MASTER_SETTING, True):
        return None
    if not getattr(settings, "MONETARY_ISSUER_KEY", ""):
        # Nothing to seal the private half with. Silent because this is the
        # ordinary state of a branch, not a fault.
        return None

    if not label:
        label = _platform_label()

    try:
        issuer = mint_issuer(label=label)
    except Exception:                                   # noqa: BLE001
        # Includes NotTheMaster from a malformed key. An ingress run for some
        # unrelated app must not die because the economy could not be set up.
        return None

    _say(reporter, f"  + monetary issuer {issuer.fingerprint[:12]}… for “{label}”")
    return issuer


def _platform_label() -> str:
    """The platform's own name, so the authority is recognisable in the UI."""
    try:
        from toto.core.models import Platform

        platform = Platform.objects.filter(active=True).first()
        if platform and platform.site_name:
            return platform.site_name
    except Exception:                                   # noqa: BLE001
        pass
    return "This platform"


# --------------------------------------------------------------------------- #
# The reserve and the currencies                                               #
# --------------------------------------------------------------------------- #

def ensure_currency_reserve():
    """The account every core currency is minted into."""
    from toto.assets.models import AccountType, LedgerAccount

    account, _created = LedgerAccount.objects.get_or_create(
        code=RESERVE_CODE,
        defaults={
            "name": RESERVE_NAME,
            "account_type": AccountType.RESERVE,
            "active": True,
            "metadata": {"kind": "currency_reserve", "system": "assarion_tpln"},
        },
    )
    return account


def ensure_core_assets(*, reporter=None) -> dict:
    """MANA, ASR and TPLN — created if absent, left alone if present.

    Returns ``{unit_name: Asset}`` for whatever exists afterwards, which on a
    host that cannot issue is simply whatever was already there.
    """
    from toto.assets.issuer import is_monetary_master
    from toto.assets.models import Asset, LedgerTransaction
    from toto.mint.services import create_currency

    present = {a.unit_name: a for a in
               Asset.objects.filter(unit_name__in=[c.unit_name for c in CORE_ASSETS])}

    missing = [c for c in CORE_ASSETS if c.unit_name not in present]
    if not missing:
        return present

    if not is_monetary_master():
        _say(reporter,
             "  · no currency seeded: this host is not the monetary master, so "
             "it mirrors what the master issued rather than engraving its own")
        return present

    reserve = ensure_currency_reserve()

    for core in missing:
        # Two independent guards, because they answer different questions. The
        # asset row says "does this currency exist"; the reference says "has its
        # opening supply already been minted". A ledger restored without its
        # assets, or an asset created by hand, must not mint a second supply.
        if LedgerTransaction.objects.filter(reference=core.reference).exists():
            existing = Asset.objects.filter(unit_name=core.unit_name).first()
            if existing is not None:
                present[core.unit_name] = existing
            continue

        asset = create_currency(
            name=core.name,
            unit_name=core.unit_name,
            total_supply=core.supply,
            decimals=core.decimals,
            reserve_account=reserve,
            reference=core.reference,
            description=core.description,
            metadata=dict(core.metadata),
        )
        asset.reserve_account = reserve
        asset.backing_document = (
            "Issued and held by the platform's Currency Reserve account.")
        asset.minting_authority = RESERVE_NAME
        asset.save(update_fields=["reserve_account", "backing_document",
                                  "minting_authority", "updated_at"])
        present[core.unit_name] = asset
        _say(reporter, f"  + asset {core.unit_name} supply={core.supply}")

    return present


# --------------------------------------------------------------------------- #
# The one entry point                                                          #
# --------------------------------------------------------------------------- #

def bootstrap_economy(*, reporter=None) -> dict:
    """Everything an install needs before anybody can be paid anything.

    Called from ``IngressCommand.handle``, so every ingress path runs it —
    ``ingress_all``, ``ingress_assets``, and ``ingress_forum`` alike. Ordering
    is forced: no issuer means no currencies, and no currencies means no
    faucets to pay them out.
    """
    from django.apps import apps as django_apps

    if not django_apps.is_installed("toto.assets"):
        return {}

    ensure_monetary_issuer(reporter=reporter)
    assets = ensure_core_assets(reporter=reporter)
    return assets
