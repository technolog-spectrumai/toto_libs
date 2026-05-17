from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from toto.game.engine import load_engine_config
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
        planet_row = demo.get("planet", {})
        planet, _ = Planet.objects.update_or_create(
            owner=empire,
            name=planet_row.get("name", "New Dawn"),
            defaults={
                "planet_type": planet_row.get("planet_type", "terran"),
                "size_class": planet_row.get("size_class", "medium"),
                "habitability": planet_row.get("habitability", 0.5),
                "gravity": planet_row.get("gravity", 1.0),
                "radiation": planet_row.get("radiation", 0.0),
                "max_provinces": planet_row.get("max_provinces", 5),
                "policy": planet_row.get("policy", "balanced"),
            },
        )

        for province_row in demo.get("provinces", []):
            province, _ = Province.objects.update_or_create(
                planet=planet,
                name=province_row["name"],
                defaults={
                    "terrain": province_row.get("terrain", "plains"),
                    "biome": province_row.get("biome", ""),
                    "cell_count": province_row.get("cell_count", 10),
                    "habitability": province_row.get("habitability", 0.5),
                    "infrastructure_level": province_row.get("infrastructure_level", 1.0),
                    "specialization": province_row.get("specialization", "none"),
                    "x": province_row.get("x", 0),
                    "y": province_row.get("y", 0),
                },
            )
            self.seed_demo_deposits(province, province_row.get("deposits", []), deposits)
            self.seed_demo_buildings(province, province_row.get("buildings", []), buildings)
            self.seed_demo_inventory(province, province_row.get("inventory", {}), items)

        self.seed_demo_transport_links(planet, demo.get("transport_links", []))

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
