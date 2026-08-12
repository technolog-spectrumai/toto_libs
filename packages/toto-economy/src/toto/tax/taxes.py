"""Tax's own levy provider: demurrage on raised time dials.

The resource is the entitlement itself — every second a user's limit sits
above its free default, summed across their grants. Deliberately
entitlement-based rather than usage-based: the bill is predictable, the free
default costs nothing, and lowering a dial stops the next day's charge.

Falling behind stops NEW dials from being raised; the ones already raised keep
working and nothing is taken back. (There used to be an enforcement pass that
deleted grants to force dials back down. It went with storage's random file
deletion — a levy collects money, and when it cannot, the answer is to stop
extending credit rather than to take things away.)
"""

from __future__ import annotations

from toto.quota.levy import LevyProvider, registry
from toto.quota.times import registry as time_registry


class TimeHoldLevy(LevyProvider):
    code = "tax.time"
    metric_code = "time.hold"
    raw_per_unit = 3600  # raw = extension seconds; billing unit = hours
    consequence_text = ("no new raised time limits until it clears — the ones "
                        "you have keep working")

    def format_raw(self, raw: int) -> str:
        return f"{raw / 3600:g} h"

    # ------------------------------------------------------------------
    # Measurement
    # ------------------------------------------------------------------

    def _extras(self, grants) -> list[tuple]:
        """[(grant, extra_seconds)] for rows whose key is still declared."""
        rows = []
        for grant in grants:
            decl = time_registry.get(grant.key)
            if decl is None:
                continue  # a key that left the registry stops billing
            extra = max(0, min(grant.seconds, decl.ceiling_seconds) - decl.free_seconds)
            if extra > 0:
                rows.append((grant, extra))
        return rows

    def _prune_stale_scopes(self, grants) -> list:
        """Drop grants whose scoped object no longer exists (defensive —
        workspace teardown clears them, but a missed path must not bill).
        One existence query per scope model. Returns the surviving grants."""
        from django.apps import apps

        by_model: dict[str, list] = {}
        survivors = []
        for grant in grants:
            decl = time_registry.get(grant.key)
            if decl is None or grant.scope_id is None or not decl.scope_model:
                survivors.append(grant)
                continue
            by_model.setdefault(decl.scope_model, []).append(grant)
        for model_label, scoped in by_model.items():
            try:
                model = apps.get_model(model_label)
            except LookupError:
                survivors.extend(scoped)  # can't verify — never delete blind
                continue
            alive = set(model.objects.filter(
                pk__in=[g.scope_id for g in scoped]).values_list("pk", flat=True))
            for grant in scoped:
                if grant.scope_id in alive:
                    survivors.append(grant)
                else:
                    grant.delete()
        return survivors

    def sample(self):
        from .models import TimeGrant

        grants = self._prune_stale_scopes(list(TimeGrant.objects.all()))
        totals: dict[int, int] = {}
        for grant, extra in self._extras(grants):
            totals[grant.user_id] = totals.get(grant.user_id, 0) + extra
        yield from totals.items()

    def measure(self, user) -> int:
        from .models import TimeGrant

        grants = self._prune_stale_scopes(list(TimeGrant.objects.filter(user=user)))
        return sum(extra for _grant, extra in self._extras(grants))


registry.register(TimeHoldLevy())
