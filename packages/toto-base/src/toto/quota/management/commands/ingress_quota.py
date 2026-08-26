"""Seed a default limit for every registered metric.

Enforcement has been dormant because no policy row existed anywhere:
``check_quota`` is silent when it finds no policy, so an unmetered metric and
an unlimited one look identical. This is what turns the limits on.

Each metric's suggested cap comes from the ``default_limit`` its own app
declared in ``<app>/metrics.py``. A metric that declares none is measured but
never capped, which is a legitimate choice and stays that way.

Rows land in the declaring app's own table — quota owns none — and one row per
metric applies to everyone, with no per-person override and — since Stations
were removed in 8/2026 — no per-institution one either.

Idempotent: an existing default policy is left exactly as it is, so re-running
after an administrator has tuned a limit will not undo their work.
"""

from decimal import Decimal

from toto.ingress import IngressCommand
from toto.quota.metrics import policy_model_for, registry


class Command(IngressCommand):
    help = "Create a default quota policy for every registered metric that declares one."

    def process(self):
        self.stdout.write("⏳  Seeding quota limits…")

        if not len(registry):
            self.stdout.write(self.style.WARNING(
                "  ⚠ no metrics registered — nothing to limit."
            ))
            return

        seeded = kept = skipped = 0
        for metric in registry.all():
            if metric.default_limit is None:
                self.stdout.write(f"  ·   {metric.code} is measured but uncapped by default")
                skipped += 1
                continue

            policy_model = policy_model_for(metric.app_label)
            if policy_model is None:
                # The app declares a metric but ships no policy table, or is not
                # installed on this host. Either way there is nowhere to put it.
                self.stdout.write(self.style.WARNING(
                    f"  ⚠ {metric.code}: {metric.app_label} has no quota table — skipped"
                ))
                skipped += 1
                continue

            # Most metrics seed as BLOCK, which is the field default. A metric
            # can ask for TRACK instead when refusing the action would do more
            # harm than the action itself — primula.save is the case: a 429
            # there loses whatever the user had not saved yet, and there is no
            # top-up path to recover from it. Counting still happens; only the
            # refusal does not. An admin can change it afterwards either way.
            defaults = {
                "name": metric.label,
                "limit": Decimal(str(metric.default_limit)),
                "unit": metric.unit,
                "period": metric.period,
                "active": True,
            }
            seed_mode = (metric.metadata or {}).get("seed_mode")
            if seed_mode:
                defaults["mode"] = seed_mode

            policy, created = policy_model.objects.get_or_create(
                metric_code=metric.code,
                defaults=defaults,
            )
            if created:
                seeded += 1
                self.stdout.write(
                    f"  +   {metric.code} ≤ {policy.limit} {metric.unit}/{metric.period}"
                )
            else:
                kept += 1
                self.stdout.write(
                    f"  ✓   {metric.code} ≤ {policy.limit} {metric.unit}/{metric.period} (kept)"
                )

        self.stdout.write(self.style.SUCCESS(
            f"✅  Quota ingress complete: {seeded} created, {kept} already set, {skipped} uncapped."
        ))
