from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from tqdm import tqdm

from toto.game.engine import load_engine_config
from toto.game.planet import generate_planet_map
from toto.game.models import (
    Building,
    BuildingStatus,
    BuildingType,
    DepositType,
    Empire,
    GameTick,
    ItemType,
    Planet,
    Province,
    ProvinceDeposit,
    ProvinceInventory,
    ProvincePopulation,
    Recipe,
    RecipeInput,
    TransportLink,
)


class Command(BaseCommand):
    help = "Seed Toto Game economy definitions from YAML and optionally create a demo empire."

    def add_arguments(self, parser):
        parser.add_argument("--config", help="Path to an economy YAML config. Defaults to toto.game/config/economy.yaml.")
        parser.add_argument("--demo-user", help="Username to receive the YAML demo world.")
        parser.add_argument("--first-user-demo", action="store_true", help="Create the demo world for the first user if one exists.")

    @transaction.atomic
    def handle(self, *args, **options):
        config = load_engine_config(options.get("config"))
        items = self.seed_items(config.get("items", []))
        deposits = self.seed_deposits(config.get("deposits", []), items)
        buildings = self.seed_buildings(config.get("buildings", []))
        self.seed_recipes(config.get("recipes", []), items, buildings)
        GameTick.objects.get_or_create(id=1)

        demo_user = options.get("demo_user")
        if options.get("first_user_demo") and not demo_user:
            demo_user = self.first_username()

        if demo_user:
            self.seed_demo_world(demo_user, config.get("demo_world", {}), items, deposits, buildings)

        self.stdout.write(self.style.SUCCESS("Toto Game economy ingress complete."))

    def first_username(self) -> str | None:
        User = get_user_model()
        user = User.objects.order_by("id").first()
        return user.username if user else None

    def seed_items(self, item_rows):
        items = {}
        for row in item_rows:
            item, _ = ItemType.objects.update_or_create(
                code=row["code"],
                defaults={
                    "name": row["name"],
                    "tier": row["tier"],
                    "cargo_class": row.get("cargo_class", "bulk"),
                    "is_energy": row.get("is_energy", False),
                    "is_strategic": row.get("is_strategic", False),
                    "base_market_value": Decimal(str(row.get("base_market_value", 1))),
                    "is_active": row.get("is_active", True),
                },
            )
            items[item.code] = item
        return items

    def seed_deposits(self, deposit_rows, items):
        deposits = {}
        for row in deposit_rows:
            item = self.required(items, row["produces_item"], "item")
            deposit, _ = DepositType.objects.update_or_create(
                code=row["code"],
                defaults={
                    "name": row["name"],
                    "produces_item": item,
                    "is_renewable": row.get("is_renewable", False),
                    "is_active": row.get("is_active", True),
                },
            )
            deposits[deposit.code] = deposit
        return deposits

    def seed_buildings(self, building_rows):
        buildings = {}
        for row in building_rows:
            building, _ = BuildingType.objects.update_or_create(
                code=row["code"],
                defaults={
                    "name": row["name"],
                    "category": row.get("category", "industry"),
                    "cell_cost": row.get("cell_cost", 1),
                    "base_energy_demand": row.get("base_energy_demand", 0),
                    "max_level": row.get("max_level", 10),
                    "is_active": row.get("is_active", True),
                },
            )
            buildings[building.code] = building
        return buildings

    def seed_recipes(self, recipe_rows, items, buildings):
        for row in recipe_rows:
            building = self.required(buildings, row["building_type"], "building")
            output_item = self.required(items, row["output_item"], "item")
            recipe, _ = Recipe.objects.update_or_create(
                code=row["code"],
                defaults={
                    "name": row["name"],
                    "building_type": building,
                    "output_item": output_item,
                    "output_quantity_per_day": row.get("output_quantity_per_day", 1),
                    "is_default": row.get("is_default", True),
                    "is_active": row.get("is_active", True),
                },
            )
            configured_inputs = set()
            for input_row in row.get("inputs", []):
                item = self.required(items, input_row["item"], "item")
                configured_inputs.add(item.id)
                RecipeInput.objects.update_or_create(
                    recipe=recipe,
                    item_type=item,
                    defaults={"quantity_per_day": input_row.get("quantity_per_day", 1)},
                )
            recipe.inputs.exclude(item_type_id__in=configured_inputs).delete()

    def seed_demo_world(self, username, demo, items, deposits, buildings):
        if not demo:
            raise CommandError("YAML config does not define demo_world.")

        User = get_user_model()
        user = User.objects.get(username=username)
        empire, _ = Empire.objects.get_or_create(user=user, defaults={"name": f"{user.username}'s Empire"})
        planet_rows = self.demo_planet_rows(demo)
        if not planet_rows:
            raise CommandError("YAML config does not define demo_world.planet or demo_world.planets.")

        for planet_row in tqdm(planet_rows, desc="Generating demo planets", unit="planet"):
            self.seed_demo_planet(empire, planet_row, items, deposits, buildings)

    def demo_planet_rows(self, demo):
        if demo.get("planets"):
            return demo["planets"]
        planet_row = dict(demo.get("planet", {}))
        if not planet_row:
            return []
        planet_row.setdefault("provinces", demo.get("provinces", []))
        planet_row.setdefault("transport_links", demo.get("transport_links", []))
        return [planet_row]

    def seed_demo_planet(self, empire, planet_row, items, deposits, buildings):
        planet, _ = Planet.objects.update_or_create(
            owner=empire,
            name=planet_row.get("name", "New Dawn"),
            defaults={
                "gravity": planet_row.get("gravity", 1.0),
                "radiation": planet_row.get("radiation", 0.0),
                "max_provinces": planet_row.get("max_provinces", 5),
                "map_size": planet_row.get("map_size", 512),
                "radius": planet_row.get("radius", 200),
                "grid_size": planet_row.get("grid_size", 12),
                "perlin_scale": planet_row.get("perlin_scale", 0.003),
                "perlin_octaves": planet_row.get("perlin_octaves", 8),
                "sea_level": planet_row.get("sea_level", 0.4),
                "climate_seed": planet_row.get("climate_seed"),
                "wind_iterations": planet_row.get("wind_iterations", 5),
                "axial_tilt": planet_row.get("axial_tilt", 23.5),
                "coriolis_factor": planet_row.get("coriolis_factor", 1.0),
                "solar_constant": planet_row.get("solar_constant", 1.0),
            },
        )

        if planet_row.get("generate_map", True):
            generate_planet_map(planet)

        generated_provinces = list(planet.provinces.order_by("id"))
        province_rows = planet_row.get("provinces", [])
        if province_rows:
            self.seed_template_provinces(planet, generated_provinces, province_rows, deposits, buildings, items)
        else:
            self.seed_generated_planet_starter(planet, planet_row.get("starter_profile", "balanced"), deposits, buildings, items)

        self.seed_demo_transport_links(planet, planet_row.get("transport_links", []))

    def seed_template_provinces(self, planet, generated_provinces, province_rows, deposits, buildings, items):
        for index, province_row in enumerate(province_rows):
            if index < len(generated_provinces):
                province = generated_provinces[index]
                if province.name != province_row["name"]:
                    province.name = province_row["name"]
                    province.save(update_fields=["name"])
            else:
                province, _ = Province.objects.update_or_create(
                    planet=planet,
                    name=province_row["name"],
                    defaults={
                        "width": province_row.get("width", 1),
                        "height": province_row.get("height", 1),
                        "avg_elev": province_row.get("avg_elev", 0.0),
                        "avg_temp": province_row.get("avg_temp", 0.5),
                        "avg_rain": province_row.get("avg_rain", 0.5),
                        "wind_speed": province_row.get("wind_speed", 0.0),
                        "cell_count": province_row.get("cell_count", 10),
                        "x": province_row.get("x", 0),
                        "y": province_row.get("y", 0),
                    },
                )
            self.seed_demo_deposits(province, province_row.get("deposits", []), deposits)
            self.seed_demo_buildings(province, province_row.get("buildings", []), buildings)
            self.seed_demo_inventory(province, province_row.get("inventory", {}), items)
            self.seed_demo_population(province, province_row.get("population"))

    def seed_generated_planet_starter(self, planet, profile_name, deposits, buildings, items):
        profiles = {
            "balanced": {
                "deposits": ["steel", "water", "hydrocarbons", "crystal", "lithium"],
                "buildings": [["habitat_grid", "solar_plant"], ["cargo_spine", "prefab_factory"], ["farm_complex"], ["chemical_plant"]],
                "inventory": {"energy": 600, "steel": 450, "water": 450, "hydrocarbons": 180, "crystal": 80},
                "population": 42000,
            },
            "water": {
                "deposits": ["water", "water", "lithium", "crystal", "steel"],
                "buildings": [["habitat_grid", "solar_plant"], ["farm_complex"], ["cargo_spine"], ["chemical_plant"]],
                "inventory": {"energy": 500, "water": 1200, "food": 350, "steel": 250, "lithium": 90},
                "population": 36000,
            },
            "dry": {
                "deposits": ["lithium", "rare_minerals", "hydrocarbons", "steel", "titanium"],
                "buildings": [["habitat_grid", "solar_plant"], ["solar_plant"], ["cargo_spine"], ["fuel_refinery"]],
                "inventory": {"energy": 900, "lithium": 260, "rare_minerals": 90, "hydrocarbons": 420, "water": 160},
                "population": 26000,
            },
            "jungle": {
                "deposits": ["water", "hydrocarbons", "steel", "crystal", "water"],
                "buildings": [["habitat_grid", "farm_complex"], ["farm_complex"], ["cargo_spine"], ["chemical_plant"]],
                "inventory": {"energy": 420, "water": 900, "food": 700, "steel": 220, "hydrocarbons": 220},
                "population": 58000,
            },
            "ice": {
                "deposits": ["water", "crystal", "rare_minerals", "titanium", "steel"],
                "buildings": [["habitat_grid", "solar_plant"], ["cargo_spine"], ["prefab_factory"], ["battery_plant"]],
                "inventory": {"energy": 700, "water": 650, "crystal": 260, "rare_minerals": 140, "steel": 320},
                "population": 18000,
            },
        }
        profile = profiles.get(profile_name, profiles["balanced"])
        provinces = list(planet.provinces.order_by("id"))
        if not provinces:
            return

        ProvinceDeposit.objects.filter(province__planet=planet).delete()
        Building.objects.filter(province__planet=planet).delete()

        for index, province in enumerate(provinces):
            deposit_code = profile["deposits"][index % len(profile["deposits"])]
            self.seed_demo_deposits(
                province,
                [
                    {
                        "deposit_type": deposit_code,
                        "abundance": 1.15 if index == 0 else 0.85,
                        "richness": 1.0,
                        "accessibility": 0.9,
                    }
                ],
                deposits,
            )

            building_rows = [{"building_type": code, "level": 2 if index == 0 and code == "solar_plant" else 1} for code in profile["buildings"][index % len(profile["buildings"])]]
            self.seed_demo_buildings(province, building_rows, buildings)
            self.seed_demo_inventory(province, profile["inventory"] if index == 0 else {}, items)
            self.seed_demo_population(province, int(profile["population"] * (1 if index == 0 else 0.25)))

    def seed_demo_deposits(self, province, deposit_rows, deposits):
        for row in deposit_rows:
            deposit_type = self.required(deposits, row["deposit_type"], "deposit")
            ProvinceDeposit.objects.update_or_create(
                province=province,
                deposit_type=deposit_type,
                defaults={
                    "abundance": row.get("abundance", 1.0),
                    "richness": row.get("richness", 1.0),
                    "accessibility": row.get("accessibility", 1.0),
                    "depletion": row.get("depletion", 0.0),
                },
            )

    def seed_demo_buildings(self, province, building_rows, buildings):
        for row in building_rows:
            building_type = self.required(buildings, row["building_type"], "building")
            Building.objects.get_or_create(
                province=province,
                building_type=building_type,
                defaults={
                    "level": row.get("level", 1),
                    "status": row.get("status", BuildingStatus.ACTIVE),
                    "priority": row.get("priority", "normal"),
                    "condition": row.get("condition", 1.0),
                },
            )

    def seed_demo_inventory(self, province, inventory_rows, items):
        for item in items.values():
            ProvinceInventory.objects.get_or_create(province=province, item_type=item)
        for code, quantity in inventory_rows.items():
            item = self.required(items, code, "item")
            ProvinceInventory.objects.update_or_create(
                province=province,
                item_type=item,
                defaults={"quantity": Decimal(str(quantity))},
            )

    def seed_demo_population(self, province, population):
        if not population:
            return
        total = int(population)
        ProvincePopulation.objects.update_or_create(
            province=province,
            defaults={
                "total_population": total,
                "laborers": int(total * 0.48),
                "technicians": int(total * 0.22),
                "engineers": int(total * 0.12),
                "scientists": int(total * 0.08),
                "administrators": int(total * 0.05),
                "happiness": 0.72,
                "health": 0.7,
                "education": 0.35,
            },
        )

    def seed_demo_transport_links(self, planet, link_rows):
        provinces = {province.name: province for province in planet.provinces.all()}
        for row in link_rows:
            from_province = self.required(provinces, row["from"], "province")
            to_province = self.required(provinces, row["to"], "province")
            TransportLink.objects.update_or_create(
                from_province=from_province,
                to_province=to_province,
                mode=row.get("mode", "road"),
                defaults={
                    "capacity_per_day": row.get("capacity_per_day", 100),
                    "condition": row.get("condition", 1.0),
                    "is_active": row.get("is_active", True),
                },
            )

    def required(self, mapping, code, kind):
        try:
            return mapping[code]
        except KeyError as exc:
            raise CommandError(f"YAML references unknown {kind}: {code}") from exc
