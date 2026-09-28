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


def top_up(user, pool, *, key: str, cap=None, reason: str = "", granted_by=None) -> str:
    """Move a member's pool toward its maximum. Never raises.

    ``cap`` (display units) bounds this one grant — the hourly regen; ``None``
    fills to the maximum — the opening fill. Returns ``"paid"``, ``"full"``,
    ``"skipped"`` (this key was already claimed) or a reason for the failure.
    Every paid grant is also a faucet payout (faucets.py): the transfer and
    the payout are written in one transaction, so the visible record and the
    ledger cannot disagree.
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
        amount = _pay(user, pool, grant, key=key, cap=cap, maximum=maximum,
                      reason=reason, granted_by=granted_by)
    except Exception as exc:                            # noqa: BLE001
        grant.detail = str(exc)[:255]
        grant.save(update_fields=["detail"])
        log.warning("mana: %s %s not topped up for %s — %s", user, pool.role, key, exc)
        return f"{user}: {exc}"
    return "paid" if amount else "full"


def _pay(user, pool, grant, *, key, cap, maximum, reason="", granted_by=None) -> int:
    from django.db import transaction

    from . import faucets

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
        grant.payout = faucets.record_payout(pool, user, key=key, amount_base_units=want, tx=tx,
                                             reason=reason, granted_by=granted_by)
        grant.save(update_fields=["amount_base_units", "transaction", "payout"])
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

    from django.utils import timezone as dj_timezone

    from toto.assets.models import FaucetRun
    from toto.assets.services.faucets import period_label

    from . import faucets

    label = period_label(at)
    report = RunReport(label=label)
    users = get_user_model().objects.filter(is_active=True).order_by("pk")
    for pool in pools().values():
        if not _payable(pool) or not pool.regen_per_hour:
            continue
        # One FaucetRun per pool per execution: the hourly faucet's own log,
        # where a retry reads "0 paid, N already done" (the faucets' rule).
        run = FaucetRun.objects.create(period_label=label, faucet=faucets.faucet_for(pool, "hourly"))
        paid = full = skipped = failed = 0
        failures = []
        try:
            for user in users.iterator():
                outcome = top_up(user, pool, key=label, cap=pool.regen_per_hour)
                if outcome == "paid":
                    paid += 1
                elif outcome == "full":
                    full += 1
                elif outcome == "skipped":
                    skipped += 1
                else:
                    failed += 1
                    failures.append(f"{pool.role}: {outcome}")
        finally:
            run.paid, run.skipped, run.failed = paid, skipped + full, failed
            run.detail = "\n".join(failures)[:20000]
            run.finished_at = dj_timezone.now()
            run.save(update_fields=["paid", "skipped", "failed", "detail", "finished_at"])
        report.paid += paid
        report.full += full
        report.skipped += skipped
        report.failed += failed
        report.failures.extend(failures)
    return report


class GrantRefused(Exception):
    """A manual grant the rules turned down: no reason, no such pool, not a
    positive amount, or the pool cannot pay."""


def grant_manual(user, pool, amount, *, reason: str, granted_by) -> str:
    """A grant by hand: toward the pool's maximum, at most ``amount``, with a
    reason and the grantor on the payout (source ``manual``). The ONE door for
    putting mana on somebody's account outside the clock and the rewards — the
    asset's Distribute button refuses mana and points here. Returns
    ``top_up``'s outcome ("paid", "full", or a failure)."""
    import uuid

    from decimal import Decimal

    if not (reason or "").strip():
        raise GrantRefused("A manual grant needs a reason.")
    try:
        amount = Decimal(str(amount))
    except Exception:  # noqa: BLE001
        raise GrantRefused("The amount is not a number.") from None
    if amount <= 0:
        raise GrantRefused("The amount must be above zero.")
    if not _payable(pool):
        raise GrantRefused(f"The {pool.role} pool cannot pay: its asset is inactive or has no reserve.")
    return top_up(user, pool, key=f"manual:{uuid.uuid4().hex[:12]}", cap=amount,
                  reason=reason.strip(), granted_by=granted_by)



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
        from . import faucets

        grant.amount_base_units = gained
        grant.transaction = tx
        grant.payout = faucets.record_payout(pool, owner, key=key, amount_base_units=gained, tx=tx)
        grant.save(update_fields=["amount_base_units", "transaction", "payout"])
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


# ---------------------------------------------------------------------------
# What a member sees
# ---------------------------------------------------------------------------
# Every number below is read from the ledger and the live rate card; nothing
# is cached here, so the chip, the pages and the JSON cannot disagree.

ICON = {"security": "fa-solid fa-shield-halved", "compute": "fa-solid fa-bolt",
        "storage": "fa-solid fa-database"}

#: The daily levies that drain a pool continuously, per role.
LEVY_OF = {"security": ("security.plain_gb_day",), "storage": ("storage.gb_day",),
           "compute": ()}


def label_of(role: str) -> str:
    from django.utils.translation import gettext as _

    return {"security": _("Security"), "compute": _("Compute"),
            "storage": _("Storage")}.get(role, role)


def next_tick_at(now=None):
    """When the next hourly refill runs (``settings.MANA_REGEN_MINUTE``, UTC)."""
    from datetime import timedelta

    from django.conf import settings
    from django.utils import timezone

    minute = int(getattr(settings, "MANA_REGEN_MINUTE", 13))
    now = (now or timezone.now()).astimezone(timezone.utc)
    tick = now.replace(minute=minute, second=0, microsecond=0)
    return tick if tick > now else tick + timedelta(hours=1)


def _price(code: str, percent: int = 0):
    """One unit's price in pool units, less ``percent`` — the member's
    community discount, which every mana charge takes off (2026-09-28)."""
    from decimal import Decimal

    from toto.quota import rates

    row = rates.price_of(code)
    if not row:
        return None
    return rates.discounted(
        Decimal(row["price_display"]) / Decimal(row.get("unit_quantity") or 1), percent)


def drain_per_day(user, role: str):
    """What this member's holdings cost the pool each day, at today's price
    and after their community discount."""
    from decimal import Decimal

    from toto.quota import rates
    from toto.quota.levy import registry as levy_registry

    percent, _source = rates.member_discount(user)
    total = Decimal(0)
    for code in LEVY_OF.get(role, ()):
        provider, price = levy_registry.get(code), _price(code, percent)
        if provider is None or price is None:
            continue
        total += Decimal(provider.measure(user)) / Decimal(provider.raw_per_unit) * price
    return total


def balances_of(user) -> dict | None:
    """``{role: {...}}`` for the chip, the pages and the JSON, or None.

    None for an anonymous caller or a host with no pools — the chip hides
    rather than showing three empty bars that would read as "you have nothing".
    """
    from decimal import Decimal

    from . import status

    if user is None or not getattr(user, "is_authenticated", False):
        return None
    found = pools()
    if not found:
        return None
    out = {}
    for role in colours.ROLES:
        pool = found.get(role)
        if pool is None:
            continue
        scale = Decimal(10) ** pool.asset.decimals
        amount = Decimal(balance_base_units(user, pool)) / scale
        drain = drain_per_day(user, role)
        regen_day = pool.regen_per_hour * 24
        net_day = regen_day - drain
        full_h, empty_h = status.eta_hours(amount, pool.max_pool, net_day / 24)
        out[role] = {
            "role": role, "label": label_of(role), "icon": ICON[role],
            "hue": colours.HUE[role], "unit": pool.asset.unit_name,
            "amount": amount, "max": pool.max_pool,
            "pct": status.pct_of(amount, pool.max_pool),
            "band": status.band_of(amount, pool.max_pool),
            "trend": status.trend_of(amount, pool.max_pool, net_day),
            "regen_per_hour": pool.regen_per_hour, "regen_per_day": regen_day,
            "drain_per_day": drain, "net_per_day": net_day,
            "eta_full_hours": full_h, "eta_empty_hours": empty_h,
        }
    return out


def lowest(balances: dict | None) -> str | None:
    """The role nearest empty — the one the chip's number shows."""
    if not balances:
        return None
    return min(balances.values(), key=lambda b: (b["pct"], b["role"]))["role"]


def _kind(tx, role) -> str:
    ref = tx.reference or ""
    if ref.startswith("mana:reward:"):
        return "reward"
    if ref.startswith(f"mana:{role}:"):
        return "signup" if ref.endswith(":signup") else "regen"
    if tx.source_type == "tariff_usage":
        code = (tx.metadata or {}).get("metric_code", "")
        return "levy" if code in {c for codes in LEVY_OF.values() for c in codes} else "charge"
    if tx.reversed_transaction_id:
        return "refund"
    return "transfer"


def history(user, role=None, limit: int = 50) -> list:
    """The member's pool movements, newest first — straight off the ledger.

    Filtered on ``LedgerEntry.asset``, never ``LedgerTransaction.asset``: a
    tariff charge leaves the transaction's asset empty, and would vanish.
    """
    from decimal import Decimal

    from toto.assets.models import LedgerEntry
    from toto.assets.prepaid import get_prepaid_account

    account = get_prepaid_account(user)
    found = pools()
    if account is None or not found:
        return []
    by_asset = {p.asset_id: p for p in found.values()
                if role is None or p.role == role}
    entries = (LedgerEntry.objects
               .filter(account=account, asset_id__in=list(by_asset))
               .select_related("transaction")
               .order_by("-created_at", "-pk")[:limit])
    from toto.assets.models import FaucetPayout

    payouts = {p.transaction_id: p for p in FaucetPayout.objects
               .filter(transaction_id__in=[e.transaction_id for e in entries])
               .select_related("faucet")}
    rows = []
    for entry in entries:
        pool = by_asset[entry.asset_id]
        tx = entry.transaction
        scale = Decimal(10) ** pool.asset.decimals
        payout = payouts.get(tx.pk)
        rows.append({
            "at": entry.created_at, "role": pool.role,
            "delta": Decimal(entry.amount_base_units) / scale,
            "kind": "manual" if payout is not None and payout.source == "manual" else _kind(tx, pool.role),
            "label": tx.description or "",
            "metric_code": (tx.metadata or {}).get("metric_code", ""),
            # The faucet that paid it (2026-09-26): every increase has one.
            "faucet": payout.faucet.name if payout is not None and payout.faucet_id else "",
            "reason": payout.reason if payout is not None else "",
        })
    return rows


def series(user, role: str, days: int = 7) -> list:
    """The pool's level at the end of each of the last ``days`` days, oldest
    first, ending now. Rebuilt by walking the ledger backwards from today's
    balance — exact, and nothing extra stored."""
    from datetime import timedelta
    from decimal import Decimal

    from django.utils import timezone

    from toto.assets.models import LedgerEntry
    from toto.assets.prepaid import get_prepaid_account

    pool = pools().get(role)
    account = get_prepaid_account(user)
    if pool is None or account is None:
        return []
    scale = Decimal(10) ** pool.asset.decimals
    now = timezone.now()
    level = Decimal(balance_base_units(user, pool)) / scale
    since = now - timedelta(days=days)
    moves = list(LedgerEntry.objects
                 .filter(account=account, asset=pool.asset, created_at__gte=since)
                 .order_by("-created_at").values_list("created_at", "amount_base_units"))
    points, i = [], 0
    for d in range(days + 1):
        at = now - timedelta(days=d)
        while i < len(moves) and moves[i][0] > at:
            level -= Decimal(moves[i][1]) / scale
            i += 1
        points.append({"at": at, "level": max(level, Decimal(0))})
    return list(reversed(points))


def plain_files(user, limit: int = 10) -> tuple[list, int]:
    """``(costliest plaintext files, how many plaintext files in all)``.

    What the "Encrypt some files…" prompt lists. The same billable set the
    plaintext levy measures, so the list and the drain cannot disagree.
    """
    from decimal import Decimal

    from toto.quota.levy import registry as levy_registry

    provider = levy_registry.get("security.plain_gb_day")
    if provider is None:
        return [], 0
    from toto.vault.models import VaultFile

    from toto.quota import rates

    qs = provider._billable(VaultFile.objects.filter(owner=user))
    total = qs.count()
    price = _price("security.plain_gb_day", rates.member_discount(user)[0]) or Decimal(0)
    rows = []
    for f in qs.order_by("-file_size_bytes", "pk")[:limit]:
        gb = Decimal(f.file_size_bytes or 0) / Decimal(provider.raw_per_unit)
        rows.append({"pk": f.pk, "title": f.title or f.key,
                     "bytes": f.file_size_bytes or 0,
                     "drain_per_day": (gb * price)})
    return rows, total
