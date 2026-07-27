"""Ingress for toto.formica — seed the default colony (idempotent)."""

from toto.ingress import IngressCommand

DEFAULT_CASTES = {
    # caste_key: default ant_count (the Queen reallocates these over time)
    "scout": 10,
    "forager": 30,
    "nurse": 10,
    "pruner": 5,
    "architect": 5,
    "queen": 1,
}


class Command(IngressCommand):
    help = "Seed the default Formica colony ('Colonia Prima') and its castes."

    def process(self):
        from ...models import CasteConfig, Colony

        self.stdout.write(self.style.WARNING("Seeding Formica..."))

        colony, created = Colony.objects.get_or_create(
            slug="colonia-prima",
            defaults={
                "name": "Colonia Prima",
                "description": (
                    "Default graph-maintenance colony: forages usefulness "
                    "trails, repairs template drift, prunes dead weight and "
                    "proposes new connections. Structural changes are "
                    "review-gated until 'trusted' is switched on."
                ),
                "enabled": True,
                "paused": False,
                "trusted": False,
                "interval_minutes": 60,
            },
        )
        self.stdout.write(
            self.style.SUCCESS("  Created colony: Colonia Prima")
            if created
            else self.style.WARNING("  Colony exists: Colonia Prima")
        )

        for caste_key, ant_count in DEFAULT_CASTES.items():
            _config, created = CasteConfig.objects.get_or_create(
                colony=colony,
                caste_key=caste_key,
                defaults={"enabled": True, "ant_count": ant_count},
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"  Caste seeded: {caste_key}"))

        self.stdout.write(self.style.SUCCESS("Formica seeding complete."))
