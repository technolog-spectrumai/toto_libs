"""Seed the levy rules.

``get_or_create`` throughout: re-running ingress never undoes a staff edit —
the same discipline as ``ingress_tariffs``. Deliberately seeds NO price: an
automatic levy must be armed by a person at /quota/<metric>/, never by a deploy.
Until then the nightly run measures and records, and charges nothing.
"""

from django.apps import apps

from toto.ingress import IngressCommand

from ...models import TaxRule


class Command(IngressCommand):
    help = "Seed levy rules, UNARMED (arming is a deliberate staff act, with a price)."

    def process(self):
        rule, created = TaxRule.objects.get_or_create(
            metric_code="storage.gb_day",
            defaults={
                "unit_label": "GB",
                # UNARMED on creation, and this is the whole point. A rule that
                # arrives active with no price measures every user every night,
                # writes a usage event, and bills zero — armed to all
                # appearances and earning nothing. `ingress_tariffs` leaves both
                # levy metrics out of its seed on purpose ("must be armed by a
                # person, never by a deploy"), so seeding the rule ACTIVE was
                # the deploy doing exactly the half it was told not to.
                #
                # The rule still arrives, with its wording, so there is
                # something to arm. Arming it is one tick and a price, in one
                # form, on the metered thing — and `levies.set_armed` refuses
                # to arm it without one.
                "active": False,
                "description": (
                    "Daily fee on stored data, charged per GB per day from "
                    "the first byte. There is no free allowance anywhere: a "
                    "metric with no price is free, for everyone."
                ),
            },
        )
        self.stdout.write(
            f"tax: rule storage.gb_day {'created' if created else 'kept'} "
            f"({'armed' if rule.active else 'UNARMED'}, per {rule.unit_label})"
        )

        hold_rule, hold_created = TaxRule.objects.get_or_create(
            metric_code="time.hold",
            defaults={
                "unit_label": "h",
                "active": False,   # unarmed on creation — see storage.gb_day above

                "description": (
                    "Rent on raised time dials. Every limit has a free "
                    "default; the extension above it is charged per hour per "
                    "day, whether used or not. The free default IS the free "
                    "tier — it costs nothing and needs no allowance."
                ),
            },
        )
        self.stdout.write(
            f"tax: rule time.hold {'created' if hold_created else 'kept'} "
            f"({'armed' if hold_rule.active else 'UNARMED'}, per {hold_rule.unit_label})"
        )

        # NO gitea.gb_day RULE. Hosted-git storage was billed per GB per day
        # here until 2026-09-05. It was removed because the forge is already
        # sold per SEAT — `gitea` and `repo` are Professional-plan entitlements
        # — so the levy was a second charge on one feature. It had also never
        # charged anybody: seeded `active=False` and never given a price.
        #
        # The forge still has a storage CAP (toto.gitea's nightly task): over
        # it, no new repositories. That is disk safety, not billing, and it is
        # unaffected by this removal.

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
                        f"tax: {code} has a rule and no price, so it is UNARMED. "
                        "Set a price and tick it armed, in one form, at "
                        "/quota/<metric>/ to start levying."
                    )
