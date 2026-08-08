"""Seed the levy rules.

``get_or_create`` throughout: re-running ingress never undoes a staff edit —
the same discipline as ``ingress_tariffs``. Deliberately seeds NO price: an
automatic levy must be armed by a person at /quota/rates/, never by a deploy.
Until then the nightly run measures and records, and charges nothing.
"""

from decimal import Decimal

from django.apps import apps

from toto.ingress import IngressCommand

from ...models import TaxRule


class Command(IngressCommand):
    help = "Seed levy rules (allowances only — prices stay a deliberate staff act)."

    def process(self):
        rule, created = TaxRule.objects.get_or_create(
            metric_code="storage.gb_day",
            defaults={
                "allowance": Decimal("1"),
                "unit_label": "GB",
                "active": True,
                "description": (
                    "Daily fee on stored data. Everyone keeps the allowance "
                    "for free; only the excess is charged, per GB per day."
                ),
            },
        )
        self.stdout.write(
            f"tax: rule storage.gb_day {'created' if created else 'kept'} "
            f"(free ≤ {rule.allowance} {rule.unit_label})"
        )

        hold_rule, hold_created = TaxRule.objects.get_or_create(
            metric_code="time.hold",
            defaults={
                "allowance": Decimal("0"),
                "unit_label": "h",
                "active": True,
                "description": (
                    "Demurrage on raised time dials. Every limit has a free "
                    "default; the extension above it is charged per hour per "
                    "day, whether used or not. Allowance 0 is deliberate: an "
                    "extension is an opt-in above an already-free default."
                ),
            },
        )
        self.stdout.write(
            f"tax: rule time.hold {'created' if hold_created else 'kept'} "
            f"(allowance {hold_rule.allowance} {hold_rule.unit_label})"
        )

        # Repair the billing-unit mirrors the tariffs seeder creates with an
        # empty dimension — these are the platform's capacity×time units and
        # the rate card should say so (precedent: storage.mb_hour in the
        # demo seeder).
        if apps.is_installed("toto.tariffs"):
            from toto.tariffs.models import BillingUnit

            for code, label, dimension in (
                ("gb_day", "Gigabyte-day", "storage_time"),
                ("hour_day", "Hour-day", "time_hold"),
            ):
                unit = BillingUnit.objects.filter(code=code).first()
                if unit is not None and (unit.dimension != dimension
                                         or unit.label != label):
                    unit.label = label
                    unit.dimension = dimension
                    unit.save(update_fields=["label", "dimension"])
                    self.stdout.write(f"tax: repaired BillingUnit {code} ({label}, {dimension})")

            from toto.quota import rates

            card = rates.rate_card()
            for code in ("storage.gb_day", "time.hold"):
                if code not in card:
                    self.stdout.write(
                        f"tax: {code} is metered but FREE — price it at "
                        "/quota/rates/ to arm the levy."
                    )
