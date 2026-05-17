from decimal import Decimal

from .models import Recipe, TransportLink


TERRAIN_COLORS = {
    "desert": "#eab308",
    "volcanic": "#ef4444",
    "forest": "#22c55e",
    "oceanic": "#0ea5e9",
    "mountain": "#94a3b8",
    "tundra": "#67e8f9",
    "steppe": "#84cc16",
    "plains": "#10b981",
}

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


def get_planet_graph_payload(planet):
    nodes = []
    edges = []

    provinces = list(
        planet.provinces.prefetch_related(
            "buildings__building_type",
            "deposits__deposit_type__produces_item",
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
                "terrain": province.get_terrain_display(),
                "specialization": province.get_specialization_display(),
                "cellUse": f"{province.used_cells}/{province.cell_count}",
                "x": province.x if province.x or province.y else index % 4,
                "y": province.y if province.x or province.y else index // 4,
                "size": 16 + min(10, province.cell_count / 2),
                "color": TERRAIN_COLORS.get(province.terrain, "#38bdf8"),
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
            "terrain": TERRAIN_COLORS,
            "transport": TRANSPORT_COLORS,
            "buildings": BUILDING_COLORS,
        },
    }
