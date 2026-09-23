"""The mana app's read and write surface. Every model import is lazy.

Everything a member sees is read from the ordinary ledger: a pool's balance is
the member's ``AssetHolding`` in that pool's asset, and a pool's history is the
``LedgerEntry`` rows that moved it. Nothing here stores a second number that
could disagree with the ledger.
"""

from __future__ import annotations

from . import colours


# ---------------------------------------------------------------------------
# The bindings
# ---------------------------------------------------------------------------

def pools() -> dict:
    """``{role: ManaPool}`` — empty before the first ingress, or on a branch."""
    from .models import ManaPool

    return {p.role: p for p in ManaPool.objects.select_related("asset")}


def roles() -> dict:
    """``{role: Asset}`` — the functional bindings."""
    return {role: pool.asset for role, pool in pools().items()}


def colour_of(metric_code: str) -> str | None:
    return colours.COLOUR_OF.get(metric_code)


def asset_for(metric_code: str):
    """The asset a metric is priced in, or None when it is not mana."""
    role = colour_of(metric_code)
    if role is None:
        return None
    pool = pools().get(role)
    return pool.asset if pool is not None else None


def pool_asset_ids() -> set:
    """Pks of the assets pools are bound to — empty where no pool exists."""
    return {pool.asset_id for pool in pools().values()}


def pooled_codes() -> set:
    """Metric codes that a pool on THIS host can price.

    Not simply ``COLOUR_OF``: on a host with the app but no pools yet (a branch,
    or before the first issue) a mapped metric has no pool to be priced in, and
    the gas rate card must still be free to price it.
    """
    present = set(pools())
    return {code for code, role in colours.COLOUR_OF.items() if role in present}


def is_mana_asset(asset) -> str | None:
    """The role an asset is bound to, or None."""
    if asset is None:
        return None
    for role, pool in pools().items():
        if pool.asset_id == asset.pk:
            return role
    return None


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

def seed_prices() -> dict:
    """``{code: Decimal}`` to seed: ``colours.PRICES`` with the host's
    ``MANA_PRICES`` over it (``None`` removes one). ``MANA_SEED_PRICES = False``
    seeds none — the same two dials ``ingress_tariffs`` offers for gas."""
    from decimal import Decimal

    from django.conf import settings

    if not getattr(settings, "MANA_SEED_PRICES", True):
        return {}
    prices = dict(colours.PRICES)
    for code, value in (getattr(settings, "MANA_PRICES", None) or {}).items():
        if value is None:
            prices.pop(code, None)
        else:
            prices[code] = Decimal(str(value))
    return prices


def audit() -> tuple[list, list]:
    """``(undecided, mapped_but_not_metered_here)``.

    Undecided — registered here, neither coloured nor exempted — is the one that
    matters: such a metric runs free, silently. The second list is information:
    a host that does not install mail has nothing to price for it.
    """
    from toto.quota.metrics import registry

    registered = set(registry.codes())
    undecided = sorted(registered - set(colours.COLOUR_OF) - colours.NOT_MANA)
    absent = sorted(set(colours.COLOUR_OF) - registered)
    return undecided, absent


# ---------------------------------------------------------------------------
# Refill — faucets.py's shape, with a ceiling
# ---------------------------------------------------------------------------
# Two layers of idempotency, exactly as the faucet sweep has them: a
# ManaGrant claim row the database referees (unique user/role/key), written
# BEFORE money moves; and a unique ledger reference on the transfer itself.
# A double fire, an overlapping worker and a manual run beside the beat are
# therefore all free.
#
# The difference from a faucet is the ceiling: a pool tops up TOWARD its
# maximum and never past it, so the holding is locked and read inside the
# same transaction that pays. And a member who is already full is not
# claimed at all — nothing moves, so there is nothing to make idempotent, and
# an hourly row per full member per pool would be a table that only grows.

import logging
from dataclasses import dataclass, field

log = logging.getLogger("toto.mana")


@dataclass
class RunReport:
    """What one refill run did. Returned, never raised."""

    label: str = ""
    paid: int = 0
    full: int = 0
    skipped: int = 0
    failed: int = 0
    failures: list = field(default_factory=list)

    def __str__(self):
        return (f"{self.label}: {self.paid} topped up, {self.full} already full, "
                f"{self.skipped} already done, {self.failed} failed")


def reference_for(user, role: str, key: str) -> str:
    """The ledger reference a grant uses. One function, because the transfer
    that writes it and the reconciliation that looks for it must agree."""
    return f"mana:{role}:{user.pk}:{key}"


def balance_base_units(user, pool, *, lock=False) -> int:
    """What this member holds in this pool, in base units. 0 with no account."""
    from toto.assets.models import AssetHolding
    from toto.assets.prepaid import get_prepaid_account

    account = get_prepaid_account(user)
    if account is None:
        return 0
    rows = AssetHolding.objects.filter(account=account, asset=pool.asset)
    if lock:
        rows = rows.select_for_update()
    holding = rows.first()
    return holding.balance_base_units if holding else 0


def top_up(user, pool, *, key: str, cap=None) -> str:
    """Move a member's pool toward its maximum. Never raises.

    ``cap`` (display units) bounds this one grant — the hourly regen; ``None``
    fills to the maximum — the opening fill. Returns ``"paid"``, ``"full"``,
    ``"skipped"`` (this key was already claimed) or a reason for the failure.
    """
    from django.db import IntegrityError, transaction

    from toto.assets.models import to_base_units

    from .models import ManaGrant

    maximum = to_base_units(pool.max_pool, pool.asset.decimals)
    if balance_base_units(user, pool) >= maximum:
        return "full"

    try:
        with transaction.atomic():
            grant = ManaGrant.objects.create(user=user, role=pool.role, key=key)
    except IntegrityError:
        _reconcile(user, pool, key)
        return "skipped"
    except Exception as exc:                            # noqa: BLE001
        log.exception("mana: could not claim %s %s for %s", pool.role, key, user)
        return f"{user}: {exc}"

    try:
        amount = _pay(user, pool, grant, key=key, cap=cap, maximum=maximum)
    except Exception as exc:                            # noqa: BLE001
        grant.detail = str(exc)[:255]
        grant.save(update_fields=["detail"])
        log.warning("mana: %s %s not topped up for %s — %s", user, pool.role, key, exc)
        return f"{user}: {exc}"
    return "paid" if amount else "full"


def _pay(user, pool, grant, *, key, cap, maximum) -> int:
    from django.db import transaction

    from toto.assets.models import AssetHolding, from_base_units, to_base_units
    from toto.assets.prepaid import get_or_create_prepaid_account
    from toto.assets.services.assets import distribute_asset

    account, _ = get_or_create_prepaid_account(user)
    with transaction.atomic():
        # The row must exist to be locked; locking it is what makes "room left"
        # a fact for the rest of this transaction rather than a guess.
        AssetHolding.objects.get_or_create(account=account, asset=pool.asset)
        held = balance_base_units(user, pool, lock=True)
        room = maximum - held
        want = room if cap is None else min(to_base_units(cap, pool.asset.decimals), room)
        if want <= 0:
            grant.detail = "pool full"
            grant.save(update_fields=["detail"])
            return 0
        tx = distribute_asset(
            asset=pool.asset, recipient_account=account,
            amount=from_base_units(want, pool.asset.decimals),
            reference=reference_for(user, pool.role, key),
            description=f"{pool.asset.name}: refill ({key})",
            metadata={"kind": "mana", "role": pool.role, "user_pk": user.pk,
                      "key": key},
        )
        grant.amount_base_units = want
        grant.transaction = tx
        grant.save(update_fields=["amount_base_units", "transaction"])
    return want


def _reconcile(user, pool, key: str) -> None:
    """Attach the transfer to a claim that lost it (a crash between the two).

    The faucet's ``_reconcile``, for the same one gap: the claim survives so
    nobody is paid twice, but a claim with no transaction reads like "never
    attempted". The ledger is what actually moved, so it is what we believe.
    """
    from toto.assets.models import LedgerTransaction

    from .models import ManaGrant

    grant = ManaGrant.objects.filter(user=user, role=pool.role, key=key,
                                     transaction__isnull=True).first()
    if grant is None:
        return
    tx = LedgerTransaction.objects.filter(
        reference=reference_for(user, pool.role, key)).first()
    if tx is None:
        return
    grant.transaction = tx
    grant.detail = "reconciled from the ledger"
    grant.save(update_fields=["transaction", "detail"])


def _payable(pool) -> bool:
    return bool(pool.asset.active and pool.asset.reserve_account_id)


def fill_pools(user, *, reason: str = "signup") -> int:
    """Fill every pool to its maximum, once per ``reason``. Returns how many
    pools moved. Never raises — a signup must not fail on the economy."""
    moved = 0
    try:
        for pool in pools().values():
            if _payable(pool) and top_up(user, pool, key=reason) == "paid":
                moved += 1
    except Exception:                                   # noqa: BLE001
        log.exception("mana: could not fill pools for %s", user)
    return moved


def regenerate_hour(*, at=None) -> RunReport:
    """Top every active member up by one hour's refill, toward the maximum.

    The hour comes from the clock (or ``at``), hour-aligned in UTC — the
    faucet's own label, so the two sweeps can never disagree about which hour
    it is. A missed hour is never backfilled.
    """
    from django.contrib.auth import get_user_model

    from toto.assets.services.faucets import period_label

    label = period_label(at)
    report = RunReport(label=label)
    users = get_user_model().objects.filter(is_active=True).order_by("pk")
    for pool in pools().values():
        if not _payable(pool) or not pool.regen_per_hour:
            continue
        for user in users.iterator():
            outcome = top_up(user, pool, key=label, cap=pool.regen_per_hour)
            if outcome == "paid":
                report.paid += 1
            elif outcome == "full":
                report.full += 1
            elif outcome == "skipped":
                report.skipped += 1
            else:
                report.failed += 1
                report.failures.append(f"{pool.role}: {outcome}")
    return report


# ---------------------------------------------------------------------------
# Earning — encrypting a file refills security mana
# ---------------------------------------------------------------------------

def reward_encrypt(vault_file, *, at=None):
    """Credit the owner's security pool for encrypting a file. Never raises.

    Once per file per UTC day (the claim key), at most ``ENCRYPT_DAILY_CAP``
    earned this way per day, never past the pool's maximum. The holding is
    locked before today's earnings are summed, so two encryptions finishing
    together are serialised and cannot both slip under the cap.

    Returns the ledger transaction, or None when nothing was paid.
    """
    try:
        return _reward_encrypt(vault_file, at=at)
    except Exception:                                   # noqa: BLE001
        log.exception("mana: encrypt reward failed for file %s",
                      getattr(vault_file, "pk", "?"))
        return None


def _reward_encrypt(vault_file, *, at=None):
    from django.db import IntegrityError, transaction
    from django.db.models import Sum
    from django.utils import timezone

    from toto.assets.models import (AssetHolding, from_base_units,
                                    to_base_units)
    from toto.assets.prepaid import get_or_create_prepaid_account
    from toto.assets.services.assets import distribute_asset

    from .models import ManaGrant

    pool = pools().get("security")
    owner = getattr(vault_file, "owner", None)
    if pool is None or owner is None or not _payable(pool):
        return None

    day = (at or timezone.now()).astimezone(timezone.utc).date().isoformat()
    key = f"encrypt:{vault_file.pk}:{day}"
    try:
        with transaction.atomic():
            grant = ManaGrant.objects.create(user=owner, role=pool.role, key=key)
    except IntegrityError:
        return None                                     # this file, today: done

    decimals = pool.asset.decimals
    account, _ = get_or_create_prepaid_account(owner)
    with transaction.atomic():
        AssetHolding.objects.get_or_create(account=account, asset=pool.asset)
        held = balance_base_units(owner, pool, lock=True)
        earned = (ManaGrant.objects
                  .filter(user=owner, role=pool.role, key__startswith="encrypt:",
                          key__endswith=f":{day}")
                  .aggregate(total=Sum("amount_base_units"))["total"] or 0)
        cap_left = to_base_units(colours.ENCRYPT_DAILY_CAP, decimals) - earned
        room = to_base_units(pool.max_pool, decimals) - held
        gained = min(to_base_units(colours.ENCRYPT_REWARD, decimals), cap_left, room)
        if gained <= 0:
            grant.detail = "daily cap reached" if cap_left <= 0 else "pool full"
            grant.save(update_fields=["detail"])
            return None
        tx = distribute_asset(
            asset=pool.asset, recipient_account=account,
            amount=from_base_units(gained, decimals),
            reference=f"mana:reward:encrypt:{vault_file.pk}:{day}",
            description=f"{pool.asset.name}: encrypted "
                        f"{getattr(vault_file, 'title', '') or 'a file'}",
            metadata={"kind": "mana", "role": pool.role, "user_pk": owner.pk,
                      "reward": "encrypt", "file_pk": vault_file.pk},
        )
        grant.amount_base_units = gained
        grant.transaction = tx
        grant.save(update_fields=["amount_base_units", "transaction"])
    return tx


# ---------------------------------------------------------------------------
# Refusal wording
# ---------------------------------------------------------------------------

def _label(role: str) -> str:
    from django.utils.translation import gettext as _

    return {"security": _("security mana"), "compute": _("compute mana"),
            "storage": _("storage mana")}.get(role, role)


def _amount(value) -> str:
    """A pool amount for a sentence: whole numbers bare, else two places."""
    from decimal import ROUND_DOWN, Decimal

    value = Decimal(value)
    if value == value.to_integral_value():
        return str(value.to_integral_value())
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_DOWN).normalize())


def explain_shortfall(user, asset, needed_base_units: int,
                      have_base_units: int) -> str | None:
    """The refusal sentence for a charge in a pool's asset, or None.

    Says which pool, what it needed and had, and when it will be enough again
    at the refill rate — the question a member actually has at a refusal.
    """
    import math
    from decimal import Decimal

    from django.utils.translation import gettext as _

    role = is_mana_asset(asset)
    if role is None:
        return None
    pool = pools()[role]
    scale = Decimal(10) ** asset.decimals
    needed = Decimal(needed_base_units) / scale
    have = Decimal(have_base_units) / scale
    sentence = _("Not enough %(mana)s: this needs %(needed)s and you have "
                 "%(have)s.") % {"mana": _label(role), "needed": _amount(needed),
                                 "have": _amount(have)}
    if needed > pool.max_pool:
        return sentence + " " + _(
            "That is more than a full pool holds (%(max)s), so it cannot be "
            "paid by waiting.") % {"max": _amount(pool.max_pool)}
    if pool.regen_per_hour > 0:
        hours = max(1, math.ceil((needed - have) / pool.regen_per_hour))
        sentence += " " + _(
            "It refills %(regen)s an hour — enough again in about %(hours)s h."
        ) % {"regen": _amount(pool.regen_per_hour), "hours": hours}
    return sentence
