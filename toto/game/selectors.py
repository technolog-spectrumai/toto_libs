from decimal import Decimal
import math
from statistics import mean

from .biomes import BIOMES
from .models import BuildingStatus, Recipe, TransportLink


BIOME_COLORS = {code: biome.color for code, biome in BIOMES.items()}
BIOME_LEGEND = [
    {
        "code": code,
        "name": biome.name,
        "color": biome.color,
    }
    for code, biome in BIOMES.items()
]

BUILDING_COLORS = {
    "extraction": "#a16207",
    "energy": "#facc15",
    "industry": "#64748b",
    "high_tech": "#8b5cf6",
    "food": "#22c55e",
    "logistics": "#06b6d4",
    "population": "#f472b6",
    "research": "#38bdf8",
    "orbital": "#f97316",
}

TRANSPORT_COLORS = {
    "road": "#94a3b8",
    "rail": "#f8fafc",
    "pipeline": "#38bdf8",
    "grid": "#facc15",
}


TRANSPORT_LABELS = {
    "road": "Cargo Road",
    "rail": "Mass Cargo",
    "pipeline": "Pipeline",
    "grid": "Power Grid",
}


def color_scale(value, stops):
    value = max(0.0, min(1.0, float(value or 0)))
    for threshold, color in stops:
        if value <= threshold:
            return color
    return stops[-1][1]


def province_population(province):
    return getattr(getattr(province, "population", None), "total_population", 0)


def province_resource_score(province):
    deposit_score = sum(deposit.extraction_factor for deposit in province.deposits.all())
    inventory_score = sum(float(row.quantity) * float(row.item_type.base_market_value) for row in province.inventory.all())
    return min(1.0, (deposit_score / 5.0) + (inventory_score / 50000.0))


def province_production_score(province):
    output = 0.0
    for building in province.buildings.all():
        row = get_building_daily_output(building)
        if row:
            output += row["quantity_per_day"] * float(row["item"].base_market_value)
    return min(1.0, output / 1000.0)


def infer_link_mode(source, target):
    source_categories = {building.building_type.category for building in source.buildings.all()}
    target_categories = {building.building_type.category for building in target.buildings.all()}
    categories = source_categories | target_categories
    if "energy" in source_categories and "energy" in target_categories:
        return "grid"
    if "industry" in categories or "high_tech" in categories:
        return "rail"
    if "extraction" in categories:
        return "pipeline"
    return "road"


def inferred_transport_edges(provinces):
    edges = []
    seen = set()
    ordered = sorted(provinces, key=lambda province: (province.y, province.x, province.id))

    for province in ordered:
        candidates = [candidate for candidate in ordered if candidate.id != province.id]
        candidates.sort(key=lambda candidate: math.dist((province.x, province.y), (candidate.x, candidate.y)))
        for target in candidates[:2]:
            key = tuple(sorted((province.id, target.id)))
            if key in seen:
                continue
            seen.add(key)
            mode = infer_link_mode(province, target)
            distance = math.dist((province.x, province.y), (target.x, target.y))
            capacity = max(25, round(220 - min(distance, 180), 2))
            edges.append(
                {
                    "id": f"inferred-{province.id}-{target.id}",
                    "source": f"province-{province.id}",
                    "target": f"province-{target.id}",
                    "label": TRANSPORT_LABELS.get(mode, "Corridor"),
                    "mode": mode,
                    "capacity": capacity,
                    "size": max(1, min(5, capacity / 60)),
                    "color": TRANSPORT_COLORS.get(mode, "#cbd5e1"),
                    "inferred": True,
                }
            )

    return edges


def get_planet_resource_summary(planet):
    summary = {}
    for province in planet.provinces.all():
        for row in province.inventory.all():
            item_id = row.item_type_id
            summary.setdefault(item_id, {"item": row.item_type, "quantity": Decimal("0"), "capacity": Decimal("0")})
            summary[item_id]["quantity"] += row.quantity
            summary[item_id]["capacity"] += row.capacity
    return sorted(summary.values(), key=lambda x: (x["item"].tier, x["item"].name))


def get_recipe_for_building(building):
    recipe = building.selected_recipe or Recipe.objects.filter(building_type=building.building_type, is_default=True, is_active=True).first()
    return recipe


def get_building_daily_output(building):
    recipe = get_recipe_for_building(building)
    if not recipe:
        return None
    return {
        "recipe": recipe,
        "item": recipe.output_item,
        "quantity_per_day": recipe.output_quantity_per_day * building.level_multiplier * building.efficiency,
    }


def get_province_production_summary(province):
    return [{"building": b, "output": get_building_daily_output(b)} for b in province.buildings.select_related("building_type", "selected_recipe")]


def get_planet_production_summary(planet):
    rows = []
    for province in planet.provinces.all():
        for row in get_province_production_summary(province):
            row["province"] = province
            rows.append(row)
    return rows


def get_planet_building_summary(planet):
    rows = []
    for province in planet.provinces.all():
        for building in province.buildings.select_related("building_type", "selected_recipe"):
            rows.append(
                {
                    "province": province,
                    "building": building,
                    "output": get_building_daily_output(building),
                }
            )
    return sorted(rows, key=lambda row: (row["province"].name, row["building"].building_type.category, row["building"].building_type.name))


def get_planet_executive_summary(planet):
    provinces = list(planet.provinces.all())
    populations = [getattr(province, "population", None) for province in provinces]
    populations = [population for population in populations if population is not None]
    total_population = sum(population.total_population for population in populations)

    if total_population:
        satisfaction = sum(population.total_population * population.happiness for population in populations) / total_population
    elif populations:
        satisfaction = mean(population.happiness for population in populations)
    else:
        satisfaction = 0

    energy_stored = Decimal("0.0000")
    energy_production = Decimal("0.0000")
    energy_consumption = Decimal("0.0000")

    for province in provinces:
        for row in province.inventory.all():
            if row.item_type.is_energy:
                energy_stored += row.quantity

        for building in province.buildings.select_related("building_type", "selected_recipe"):
            if building.status != BuildingStatus.ACTIVE:
                continue

            recipe = get_recipe_for_building(building)
            multiplier = building.level_multiplier * building.efficiency
            energy_consumption += Decimal(str(building.building_type.base_energy_demand * multiplier))

            if not recipe:
                continue

            if recipe.output_item.is_energy:
                energy_production += Decimal(str(recipe.output_quantity_per_day * multiplier))

            for recipe_input in recipe.inputs.select_related("item_type"):
                if recipe_input.item_type.is_energy:
                    energy_consumption += Decimal(str(recipe_input.quantity_per_day * multiplier))

    energy_net = energy_production - energy_consumption
    return {
        "credits": planet.owner.credits if planet.owner_id else Decimal("0.00"),
        "population": total_population,
        "satisfaction": satisfaction,
        "satisfaction_percent": round(satisfaction * 100, 1),
        "energy_stored": energy_stored,
        "energy_production_per_day": energy_production,
        "energy_consumption_per_day": energy_consumption,
        "energy_net_per_day": energy_net,
        "energy_balance_label": "Surplus" if energy_net >= 0 else "Deficit",
    }


def metric_summary(values):
    values = [float(value or 0) for value in values]
    if not values:
        return {"avg": 0, "min": 0, "max": 0}
    return {
        "avg": round(mean(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def get_planet_climate_summary(planet):
    provinces = list(planet.provinces.all())
    biome_counts = {}
    for province in provinces:
        biome_counts[province.biome] = biome_counts.get(province.biome, 0) + 1

    return {
        "elevation": metric_summary([province.avg_elev for province in provinces]),
        "temperature": metric_summary([province.avg_temp for province in provinces]),
        "rainfall": metric_summary([province.avg_rain for province in provinces]),
        "wind": metric_summary([province.wind_speed for province in provinces]),
        "habitability": metric_summary([province.habitability for province in provinces]),
        "infrastructure": metric_summary([province.infrastructure_level for province in provinces]),
        "biomes": sorted(biome_counts.items(), key=lambda row: (-row[1], row[0])),
    }


def get_planet_graph_payload(planet):
    nodes = []
    edges = []

    provinces = list(
        planet.provinces.prefetch_related(
            "buildings__building_type",
            "buildings__selected_recipe",
            "inventory__item_type",
            "deposits__deposit_type__produces_item",
            "population",
        )
    )
    province_ids = {province.id for province in provinces}

    for index, province in enumerate(provinces):
        deposits = [
            {
                "name": deposit.deposit_type.name,
                "item": deposit.deposit_type.produces_item.name,
                "factor": round(deposit.extraction_factor, 3),
            }
            for deposit in province.deposits.all()
        ]
        buildings = [
            {
                "id": f"building-{building.id}",
                "label": building.building_type.name,
                "category": building.building_type.category,
                "level": building.level,
                "status": building.status,
                "efficiency": round(building.efficiency, 3),
                "color": BUILDING_COLORS.get(building.building_type.category, "#cbd5e1"),
            }
            for building in province.buildings.all()
        ]
        nodes.append(
            {
                "id": f"province-{province.id}",
                "provinceId": province.id,
                "label": province.name,
                "kind": "province",
                "biome": province.biome,
                "biomeCode": province.biome_code,
                "habitability": round(province.habitability, 3),
                "infrastructure": round(province.infrastructure_level, 3),
                "population": province_population(province),
                "resourceScore": round(province_resource_score(province), 3),
                "productionScore": round(province_production_score(province), 3),
                "cellUse": f"{province.used_cells}/{province.cell_count}",
                "climate": {
                    "elevation": round(province.avg_elev, 3),
                    "temperature": round(province.avg_temp, 3),
                    "rainfall": round(province.avg_rain, 3),
                    "wind": round(province.wind_speed, 3),
                },
                "x": province.x if province.x or province.y else index % 4,
                "y": province.y if province.x or province.y else index // 4,
                "size": 14 + min(12, province.area / 4),
                "color": province.biome_color,
                "colors": {
                    "biome": province.biome_color,
                    "habitability": color_scale(province.habitability, [(0.25, "#ef4444"), (0.5, "#f97316"), (0.75, "#eab308"), (1.0, "#22c55e")]),
                    "infrastructure": color_scale(province.infrastructure_level / 5.0, [(0.2, "#334155"), (0.45, "#64748b"), (0.7, "#38bdf8"), (1.0, "#f8fafc")]),
                    "population": color_scale(province_population(province) / 1000000.0, [(0.2, "#312e81"), (0.45, "#7c3aed"), (0.7, "#db2777"), (1.0, "#f9a8d4")]),
                    "resources": color_scale(province_resource_score(province), [(0.2, "#44403c"), (0.45, "#a16207"), (0.7, "#eab308"), (1.0, "#fde68a")]),
                    "production": color_scale(province_production_score(province), [(0.2, "#1e293b"), (0.45, "#2563eb"), (0.7, "#06b6d4"), (1.0, "#67e8f9")]),
                },
                "deposits": deposits,
                "buildings": buildings,
            }
        )

    links = TransportLink.objects.filter(
        from_province_id__in=province_ids,
        to_province_id__in=province_ids,
        is_active=True,
    ).select_related("from_province", "to_province")

    for link in links:
        edges.append(
            {
                "id": f"transport-{link.id}",
                "source": f"province-{link.from_province_id}",
                "target": f"province-{link.to_province_id}",
                "label": link.get_mode_display(),
                "mode": link.mode,
                "capacity": round(link.effective_capacity_per_day, 2),
                "size": max(1, min(8, link.effective_capacity_per_day / 100)),
                "color": TRANSPORT_COLORS.get(link.mode, "#cbd5e1"),
            }
        )

    if not edges:
        edges.extend(inferred_transport_edges(provinces))

    return {
        "nodes": nodes,
        "edges": edges,
        "legend": {
            "biome": BIOME_LEGEND,
            "transport": TRANSPORT_COLORS,
            "buildings": BUILDING_COLORS,
        },
    }
