from decimal import Decimal
from statistics import mean

from .biomes import BIOMES
from .models import Recipe, TransportLink


BIOME_COLORS = {code: biome.color for code, biome in BIOMES.items()}

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


def get_planet_resource_summary(planet):
    summary = {}
    for province in planet.provinces.all():
        for row in province.inventory.all():
            item_id = row.item_type_id
            summary.setdefault(item_id, {"item": row.item_type, "quantity": Decimal("0"), "capacity": Decimal("0")})
            summary[item_id]["quantity"] += row.quantity
            summary[item_id]["capacity"] += row.capacity
    return sorted(summary.values(), key=lambda x: (x["item"].tier, x["item"].name))


def get_building_daily_output(building):
    recipe = building.selected_recipe or Recipe.objects.filter(building_type=building.building_type, is_default=True, is_active=True).first()
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

    return {
        "nodes": nodes,
        "edges": edges,
        "legend": {
            "biome": BIOME_COLORS,
            "transport": TRANSPORT_COLORS,
            "buildings": BUILDING_COLORS,
        },
    }
