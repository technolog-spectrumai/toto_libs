from decimal import Decimal

from django.db import transaction

from .engine import engine_value, inventory_capacity, ticks_per_day
from .models import Building, BuildingStatus, ConstructionProject, GameTick, ItemType, Planet, Province, ProvinceInventory, Recipe


def get_or_create_inventory(province: Province, item_type: ItemType) -> ProvinceInventory:
    inventory, _ = ProvinceInventory.objects.get_or_create(
        province=province,
        item_type=item_type,
        defaults={"quantity": Decimal("0.0000"), "capacity": Decimal(inventory_capacity())},
    )
    return inventory


def add_inventory(province: Province, item_type: ItemType, amount: Decimal) -> None:
    if amount <= 0:
        return
    inventory = get_or_create_inventory(province, item_type)
    inventory.quantity += amount
    if inventory.capacity > 0:
        inventory.quantity = min(inventory.quantity, inventory.capacity)
    inventory.save(update_fields=["quantity"])


def remove_inventory(province: Province, item_type: ItemType, amount: Decimal) -> Decimal:
    if amount <= 0:
        return Decimal("0.0000")
    inventory = get_or_create_inventory(province, item_type)
    removed = min(inventory.quantity, amount)
    inventory.quantity -= removed
    inventory.save(update_fields=["quantity"])
    return removed


def get_building_recipe(building: Building):
    if building.selected_recipe_id:
        return building.selected_recipe
    return Recipe.objects.filter(building_type=building.building_type, is_active=True, is_default=True).first()


def run_deposit_tick(province: Province) -> None:
    """Base economy: deposits passively produce local raw resources."""
    extraction_modifier = Decimal(str(province.biome_class.extraction_modifier))
    for deposit in province.deposits.select_related("deposit_type__produces_item"):
        item = deposit.deposit_type.produces_item
        base_output = Decimal(str(engine_value("deposit_output_per_day", 100)))
        amount_per_day = Decimal(str(deposit.extraction_factor)) * base_output * extraction_modifier
        add_inventory(province, item, amount_per_day / Decimal(ticks_per_day()))


def run_building_tick(building: Building) -> None:
    """MVP: local inventory only. TODO: add logistics, energy grid, workforce, and reserves."""
    if building.status != BuildingStatus.ACTIVE:
        building.efficiency = 0.0
        building.save(update_fields=["efficiency"])
        return

    recipe = get_building_recipe(building)
    if recipe is None:
        building.efficiency = 0.0
        building.save(update_fields=["efficiency"])
        return

    input_ratio = Decimal("1.0000")
    inputs = list(recipe.inputs.select_related("item_type"))

    for recipe_input in inputs:
        needed_per_tick = Decimal(str(recipe_input.quantity_per_day)) / Decimal(ticks_per_day())
        available = get_or_create_inventory(building.province, recipe_input.item_type).quantity
        ratio = Decimal("1.0000") if needed_per_tick <= 0 else min(Decimal("1.0000"), available / needed_per_tick)
        input_ratio = min(input_ratio, ratio)

    final_efficiency = input_ratio * Decimal(str(building.condition))
    if final_efficiency <= 0:
        building.efficiency = 0.0
        building.save(update_fields=["efficiency"])
        return

    infrastructure_bonus = Decimal(str(1 + min(0.35, building.province.infrastructure_level * 0.03)))
    biome_bonus = Decimal(str(building.province.biome_class.production_modifier))
    production_multiplier = Decimal(str(building.level_multiplier)) * final_efficiency * biome_bonus * infrastructure_bonus

    for recipe_input in inputs:
        base_needed = Decimal(str(recipe_input.quantity_per_day)) / Decimal(ticks_per_day())
        remove_inventory(building.province, recipe_input.item_type, base_needed * production_multiplier)

    base_output = Decimal(str(recipe.output_quantity_per_day)) / Decimal(ticks_per_day())
    add_inventory(building.province, recipe.output_item, base_output * production_multiplier)

    building.efficiency = float(final_efficiency)
    building.save(update_fields=["efficiency"])


def run_construction_tick(project: ConstructionProject) -> None:
    """MVP: construction advances for free. TODO: consume prefabs, steel, machinery, energy, labor."""
    if project.status != BuildingStatus.CONSTRUCTING:
        return
    construction_speed = float(engine_value("construction_work_per_tick", 1))
    construction_speed *= 1 + min(0.5, project.province.infrastructure_level * 0.04)
    project.progress += construction_speed
    if project.progress >= project.required_work:
        Building.objects.create(
            province=project.province,
            building_type=project.building_type,
            level=project.target_level,
            status=BuildingStatus.ACTIVE,
            priority=project.priority,
        )
        project.status = BuildingStatus.ACTIVE
    project.save(update_fields=["progress", "status", "updated_at"])


@transaction.atomic
def run_planet_tick(planet_id: int) -> None:
    planet = Planet.objects.select_for_update().get(id=planet_id)
    provinces = list(
        planet.provinces.prefetch_related(
            "deposits__deposit_type__produces_item",
            "buildings__building_type",
            "buildings__selected_recipe__inputs__item_type",
            "construction_projects__building_type",
        )
    )
    for province in provinces:
        run_deposit_tick(province)
    for province in provinces:
        for building in province.buildings.select_related("building_type", "selected_recipe"):
            run_building_tick(building)
    for province in provinces:
        for project in province.construction_projects.filter(status=BuildingStatus.CONSTRUCTING):
            run_construction_tick(project)


@transaction.atomic
def increment_global_tick() -> int:
    game_tick, _ = GameTick.objects.select_for_update().get_or_create(id=1)
    game_tick.current_tick += 1
    game_tick.save(update_fields=["current_tick", "updated_at"])
    return game_tick.current_tick
