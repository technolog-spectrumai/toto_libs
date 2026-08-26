"""What this platform pays in — asked in one place.

Before this module the answer was spread across the code and differed by
caller: ``settings.GAS_ASSET`` in one place, a literal ``"ASR"`` in another, a
currency contract in a third. Nothing was wrong with any of them individually;
what was wrong is that "which asset does the platform settle in" had several
answers and no way to change it once.

Every internal payment — a levy, a reward, a faucet payout, a platform fee —
resolves through :func:`settlement_asset`.

## Resolution order, and why

1. **The staff choice.** A deliberate act, recorded with who made it and when.
   It wins because somebody chose it; that is what a setting is for.
2. **The currency contract.** What a monetary master actually granted this host
   the right to bill in. It is authoritative on a branch precisely because a
   branch does not get to decide what it bills in — see
   ``toto.assets.contracts`` and portal/hierarchical_economy.md.
3. **MANA.** The default, and the reason ingress guarantees it exists.

## What this is NOT

It is not "what metered work costs" — that is
:func:`toto.tariffs.rate_card.gas_asset`, and the two are kept apart on purpose.
A platform's rate card is denominated by its currency CONTRACT and by the ticker
it was seeded under, and a host that has been billing in ASR for a year must not
silently re-denominate its whole rate card because MANA appeared. What this
answers is the other direction: what the platform PAYS — grants, rewards,
faucet payouts, internal settlements — which had no single answer at all.

Both honour a staff choice first, so choosing one currency does move everything;
they differ only in what they fall back to when nobody has chosen.

An inactive asset is skipped at every step: settling in a retired currency is
the failure this order exists to avoid, and falling through to the next answer
is better than refusing to pay anybody.
"""

from __future__ import annotations

#: The currency a platform settles in when nobody has said otherwise. Created
#: by ``toto.assets.services.bootstrap``, so it is present on every install.
DEFAULT_UNIT = "MANA"


def settlement_asset():
    """The asset this platform settles internal payments in, or None.

    None only on a platform with no currencies at all, which is the state an
    install is in before any ingress has run. Callers that must pay somebody
    should treat None as "not yet configured" and say so, rather than guessing.
    """
    from toto.assets.models import Asset, SettlementAsset

    chosen = (SettlementAsset.objects
              .select_related("asset")
              .filter(asset__active=True)
              .first())
    if chosen is not None:
        return chosen.asset

    try:
        from toto.assets.contracts import contractual_asset

        contracted = contractual_asset()
    except Exception:                                   # noqa: BLE001
        contracted = None
    if contracted is not None and contracted.active:
        return contracted

    return Asset.objects.filter(unit_name=DEFAULT_UNIT, active=True).first()


def settlement_choice():
    """The stored row, or None when the platform is running on the default.

    Separate from :func:`settlement_asset` because the UI has to tell the two
    apart: "settling in MANA because staff chose it" and "settling in MANA
    because nobody has chosen anything" look identical otherwise, and only one
    of them is a decision.
    """
    from toto.assets.models import SettlementAsset

    return SettlementAsset.objects.select_related("asset", "chosen_by").first()


def set_settlement_asset(asset, *, actor=None):
    """Record a staff choice. Idempotent, and the only writer of that row."""
    from django.core.exceptions import ValidationError
    from django.utils.translation import gettext as _

    from toto.assets.models import SettlementAsset

    if asset is None:
        raise ValidationError(_("Choose a currency to settle in."))
    if not asset.active:
        raise ValidationError(
            _("%(unit)s is not active, so the platform cannot settle in it.")
            % {"unit": asset.unit_name})
    if getattr(asset, "is_mirror", False):
        # A mirror is somebody else's currency held here as a signed copy. Its
        # reserve is on the master, so nothing on this host can pay out of it.
        raise ValidationError(
            _("%(unit)s is a mirrored currency: its reserve is on the platform "
              "that issued it, so payments cannot be made from it here.")
            % {"unit": asset.unit_name})

    row, _created = SettlementAsset.objects.update_or_create(
        singleton=1, defaults={"asset": asset, "chosen_by": actor})
    return row
