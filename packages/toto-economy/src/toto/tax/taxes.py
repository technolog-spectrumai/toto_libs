"""Tax's own levy provider: demurrage on raised time dials.

The resource is the entitlement itself — every second a user's limit sits
above its free default, summed across their grants. Deliberately
entitlement-based rather than usage-based: the bill is predictable, the free
default costs nothing, and lowering a dial stops the next day's charge.

Enforcement sheds grants LARGEST-EXTENSION-FIRST rather than randomly. The
platform's random rule exists so storage enforcement cannot be gamed toward
keeping favorites while data must be destroyed; deleting a grant destroys
nothing — it resets a dial to the free default — so randomness buys no
fairness here, while largest-first reaches the target with the fewest audit
rows.
"""

from __future__ import annotations

from toto.quota.levy import EnforcementResult, LevyProvider, registry
from toto.quota.times import registry as time_registry


class TimeHoldLevy(LevyProvider):
    code = "tax.time"
    metric_code = "time.hold"
    raw_per_unit = 3600  # raw = extension seconds; billing unit = hours
    consequence_text = ("your raised time limits will be reset to their free "
                        "defaults (nothing is deleted)")

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

    # ------------------------------------------------------------------
    # Enforcement — reset dials, largest extension first
    # ------------------------------------------------------------------

    def enforce(self, user, target_raw: int, *, rng=None,
                on_deleted=None, on_skipped=None) -> EnforcementResult:
        from django.db import transaction

        from .models import TimeGrant

        result = EnforcementResult()
        grants = self._prune_stale_scopes(list(TimeGrant.objects.filter(user=user)))
        rows = sorted(self._extras(grants), key=lambda pair: pair[1], reverse=True)
        remaining = sum(extra for _grant, extra in rows)

        for grant, extra in rows:
            if remaining <= target_raw:
                break
            info = {
                "pk": grant.pk,
                "title": self._grant_title(grant),
                "key": grant.key,
                "bucket": self._scope_label(grant),
                "size": extra,
            }
            with transaction.atomic():
                grant.delete()
                if on_deleted is not None:
                    on_deleted(info)
            result.deleted_count += 1
            result.deleted_raw += extra
            remaining -= extra

        result.final_raw = self.measure(user)
        result.reached_target = result.final_raw <= target_raw
        return result

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------

    def _scope_label(self, grant) -> str:
        decl = time_registry.get(grant.key)
        if decl is None or grant.scope_id is None or not decl.scope_model:
            return ""
        from django.apps import apps

        try:
            obj = apps.get_model(decl.scope_model).objects.filter(pk=grant.scope_id).first()
        except LookupError:
            return f"#{grant.scope_id}"
        if obj is None:
            return "(deleted)"
        return getattr(obj, "name", None) or str(obj)

    def _grant_title(self, grant) -> str:
        decl = time_registry.get(grant.key)
        label = decl.label if decl else grant.key
        scope = self._scope_label(grant)
        return f"{label} — {scope}" if scope else f"{label} — account"


registry.register(TimeHoldLevy())
