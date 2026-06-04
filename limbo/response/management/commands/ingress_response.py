from toto.ingress import IngressCommand
from toto.response.models import InterventionType


INTERVENTION_TYPES = [
    {"name": "Welfare Check",      "slug": "welfare-check",      "icon": "fa-solid fa-hand-holding-heart",       "order": 1},
    {"name": "First Aid",          "slug": "first-aid",          "icon": "fa-solid fa-kit-medical",              "order": 2},
    {"name": "Evacuation Pickup",  "slug": "evacuation-pickup",  "icon": "fa-solid fa-person-walking-arrow-right","order": 3},
    {"name": "Transport",          "slug": "transport",          "icon": "fa-solid fa-truck",                    "order": 4},
    {"name": "Supply Delivery",    "slug": "supply-delivery",    "icon": "fa-solid fa-boxes-stacked",            "order": 5},
    {"name": "Shelter Setup",      "slug": "shelter-setup",      "icon": "fa-solid fa-tent",                     "order": 6},
    {"name": "Sandbagging",        "slug": "sandbagging",        "icon": "fa-solid fa-fill-drip",                "order": 7},
    {"name": "Road Closure",       "slug": "road-closure",       "icon": "fa-solid fa-road-barrier",             "order": 8},
    {"name": "Damage Report",      "slug": "damage-report",      "icon": "fa-solid fa-clipboard-list",           "order": 9},
    {"name": "Decontamination",    "slug": "decontamination",    "icon": "fa-solid fa-biohazard",                "order": 10},
    {"name": "Communications",     "slug": "communications",     "icon": "fa-solid fa-tower-broadcast",          "order": 11},
    {"name": "Search & Rescue",    "slug": "search-rescue",      "icon": "fa-solid fa-magnifying-glass-location","order": 12},
]


class Command(IngressCommand):
    help = "Seed response app reference data (InterventionType)."

    def process(self):
        self._create_intervention_types()
        self.stdout.write(self.style.SUCCESS("✔  Response ingress complete."))

    def _create_intervention_types(self):
        for data in INTERVENTION_TYPES:
            InterventionType.objects.get_or_create(
                slug=data["slug"],
                defaults={
                    "name": data["name"],
                    "icon": data["icon"],
                    "order": data["order"],
                },
            )
        self.stdout.write(f"✔  Intervention types: {InterventionType.objects.count()} total.")
