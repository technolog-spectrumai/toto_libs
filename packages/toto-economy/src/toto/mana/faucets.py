"""Every mana increase is a faucet payout (2026-09-26).

Four faucets per pool, one per way the pool fills, all on the pool's asset
and all platform-wide (no Community):

| slug | source | pays |
|---|---|---|
| ``mana-<role>-hourly`` | scheduled | the hourly refill toward the maximum, every active member |
| ``mana-<role>-signup`` | automatic | the opening fill on arrival (and the backfill for existing members) |
| ``mana-<role>-encrypt-reward`` | automatic | the security reward for encrypting a file |
| ``mana-<role>-manual`` | manual | a grant somebody with the right made, with a reason |

The faucet rows are labels for the payouts: they have no members and the
members' sweep never pays them (``source != members``). Refunds of failed
metered work are reversals, not faucets — they give back, they do not
generate — and the history calls them refunds.
"""

from __future__ import annotations

KINDS = {
    "hourly": ("scheduled", "Hourly refill toward the maximum, every active member."),
    "signup": ("automatic", "The opening fill on arrival."),
    "encrypt-reward": ("automatic", "Earned by encrypting a file (security only)."),
    "manual": ("manual", "Granted by hand, with a reason."),
}


def slug_for(role: str, kind: str) -> str:
    return f"mana-{role}-{kind}"


def faucet_for(pool, kind: str):
    """The pool's faucet for this kind, made on first use."""
    from toto.assets.models import Faucet

    source, note = KINDS[kind]
    faucet, _ = Faucet.objects.get_or_create(
        slug=slug_for(pool.role, kind),
        defaults={"name": f"Mana {pool.role} — {kind.replace('-', ' ')}", "asset": pool.asset,
                  "active": True, "source": source, "note": note})
    return faucet


def kind_of_key(key: str) -> str:
    if key.startswith("hourly:"):
        return "hourly"
    if key == "signup":
        return "signup"
    if key.startswith("encrypt:"):
        return "encrypt-reward"
    if key.startswith("manual:"):
        return "manual"
    return "manual"


def record_payout(pool, user, *, key: str, amount_base_units: int, tx, reason: str = "",
                  granted_by=None):
    """The visible row for one increase, beside the ledger transfer. Called
    inside the transfer's transaction so the two never disagree."""
    from toto.assets.models import Faucet, FaucetPayout, FaucetPayoutStatus

    faucet = faucet_for(pool, kind_of_key(key))
    payout, _ = FaucetPayout.objects.get_or_create(
        faucet=faucet, recipient=user, period_label=key[:64],
        defaults={"amount_base_units": amount_base_units, "status": FaucetPayoutStatus.PAID,
                  "transaction": tx, "source": faucet.source, "community": faucet.community,
                  "reason": reason[:300], "granted_by": granted_by})
    return payout


def ensure_faucets(pools: dict, *, reporter=None) -> int:
    """Make every pool's four faucets exist, so the Faucets page shows the
    whole picture before the first hour has paid anybody."""
    made = 0
    for pool in pools.values():
        for kind in KINDS:
            from toto.assets.models import Faucet

            if not Faucet.objects.filter(slug=slug_for(pool.role, kind)).exists():
                faucet_for(pool, kind)
                made += 1
                if reporter is not None:
                    reporter(f"  + faucet {slug_for(pool.role, kind)}")
    return made
