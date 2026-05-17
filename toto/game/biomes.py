from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import mean


@dataclass(frozen=True)
class Province:
    x: int
    y: int
    width: int
    height: int
    avg_elev: float = 0
    avg_temp: float = 0
    avg_rain: float = 0
    biome: str = "unknown"
    wind_speed: float = 0


@dataclass(frozen=True)
class PlanetConfig:
    size: int = 512
    radius: int = 200
    grid_size: int = 12
    perlin_scale: float = 0.003
    perlin_octaves: int = 8
    sea_level: float = 0.4
    seed: int | None = None
    wind_iterations: int = 5
    axial_tilt: float = 23.5
    coriolis_factor: float = 1.0
    solar_constant: float = 1.0


@dataclass(frozen=True)
class BiomeClass:
    code: str
    name: str
    color: str
    habitability: float
    extraction_modifier: float = 1.0
    production_modifier: float = 1.0
    tags: tuple[str, ...] = ()


BIOMES = {
    "deep_ocean": BiomeClass(
        "deep_ocean", "Deep Ocean", "#061426",
        0.10, 0.80, 0.65,
        ("ocean", "deep", "water")
    ),
    "shallow_sea": BiomeClass(
        "shallow_sea", "Shallow Sea", "#1d5f73",
        0.38, 0.90, 0.80,
        ("ocean", "coastal", "water")
    ),
    "sea_ice": BiomeClass(
        "sea_ice", "Sea Ice", "#d7e5e8",
        0.16, 0.85, 0.55,
        ("ocean", "cold", "ice")
    ),
    "wetland": BiomeClass(
        "wetland", "Wetland", "#3f5f32",
        0.58, 0.95, 0.92,
        ("wet", "life", "lowland")
    ),
    "tropical_rainforest": BiomeClass(
        "tropical_rainforest", "Tropical Rainforest", "#143f1d",
        0.78, 0.85, 1.00,
        ("hot", "wet", "forest")
    ),
    "tropical_seasonal_forest": BiomeClass(
        "tropical_seasonal_forest", "Tropical Seasonal Forest", "#315728",
        0.70, 0.90, 0.95,
        ("hot", "seasonal", "forest")
    ),
    "temperate_forest": BiomeClass(
        "temperate_forest", "Temperate Forest", "#2f4f2f",
        0.76, 1.00, 1.05,
        ("temperate", "wet", "forest")
    ),
    "grassland": BiomeClass(
        "grassland", "Grassland", "#8b8a54",
        0.60, 1.02, 0.95,
        ("temperate", "grass", "semi_dry")
    ),
    "savanna": BiomeClass(
        "savanna", "Savanna", "#a08b4f",
        0.58, 1.05, 0.95,
        ("hot", "grass", "seasonal")
    ),
    "shrubland": BiomeClass(
        "shrubland", "Shrubland", "#7a6f4f",
        0.48, 1.08, 0.85,
        ("dry", "scrub", "temperate")
    ),
    "semi_arid_steppe": BiomeClass(
        "semi_arid_steppe", "Semi-Arid Steppe", "#b39a63",
        0.42, 1.10, 0.82,
        ("dry", "grass", "steppe")
    ),
    "desert": BiomeClass(
        "desert", "Desert", "#c49a5c",
        0.22, 1.15, 0.65,
        ("dry", "hot", "arid")
    ),
    "cold_desert": BiomeClass(
        "cold_desert", "Cold Desert", "#9f927c",
        0.26, 1.10, 0.70,
        ("cold", "dry", "arid")
    ),
    "tundra": BiomeClass(
        "tundra", "Tundra", "#8a9385",
        0.32, 1.00, 0.75,
        ("cold", "dry", "low_vegetation")
    ),
    "boreal_forest": BiomeClass(
        "boreal_forest", "Boreal Forest", "#213f2c",
        0.62, 0.95, 0.90,
        ("cold", "forest", "conifer")
    ),
    "alpine": BiomeClass(
        "alpine", "Alpine", "#8f8e86",
        0.34, 1.22, 0.78,
        ("highland", "cold", "rocky")
    ),
    "bare_rock": BiomeClass(
        "bare_rock", "Bare Rock", "#625f58",
        0.24, 1.30, 0.75,
        ("rocky", "barren")
    ),
    "unknown": BiomeClass(
        "unknown", "Unknown", "#4f5963",
        0.40
    ),
}

def clamp(value: float, floor: float = 0.0, ceiling: float = 1.0) -> float:
    return max(floor, min(ceiling, value))


def get_biome(code: str | None) -> BiomeClass:
    return BIOMES.get((code or "unknown").strip().lower(), BIOMES["unknown"])


def classify_biome(province: Province, sea_level: float = 0.4) -> BiomeClass:
    elev = province.avg_elev
    temp = province.avg_temp
    rain = province.avg_rain
    wind = province.wind_speed

    # Ocean
    if elev < sea_level - 0.18:
        return BIOMES["deep_ocean"]

    if elev < sea_level:
        if temp < 0.18:
            return BIOMES["sea_ice"]
        return BIOMES["shallow_sea"]

    # Very low, wet coastal/inland land
    if elev < sea_level + 0.05 and rain > 0.68:
        return BIOMES["wetland"]

    # High elevation overrides most climate bands
    if elev > 0.78:
        if rain < 0.28:
            return BIOMES["bare_rock"]
        return BIOMES["alpine"]

    if elev > 0.65 and temp < 0.35:
        return BIOMES["alpine"]

    # Cold climates
    if temp < 0.18:
        if rain < 0.28:
            return BIOMES["cold_desert"]
        return BIOMES["tundra"]

    if temp < 0.34:
        if rain > 0.42:
            return BIOMES["boreal_forest"]
        if rain < 0.25:
            return BIOMES["cold_desert"]
        return BIOMES["tundra"]

    # Hot climates
    if temp > 0.72:
        if rain > 0.72:
            return BIOMES["tropical_rainforest"]
        if rain > 0.48:
            return BIOMES["tropical_seasonal_forest"]
        if rain > 0.26:
            return BIOMES["savanna"]
        return BIOMES["desert"]

    # Warm / temperate dry climates
    if rain < 0.18:
        if temp < 0.42:
            return BIOMES["cold_desert"]
        return BIOMES["desert"]

    if rain < 0.32:
        return BIOMES["semi_arid_steppe"]

    if rain < 0.45:
        if temp > 0.58:
            return BIOMES["savanna"]
        return BIOMES["grassland"]

    if rain < 0.58:
        if wind > 0.72:
            return BIOMES["shrubland"]
        return BIOMES["grassland"]

    # Wet temperate climates
    if temp > 0.58 and rain > 0.68:
        return BIOMES["tropical_seasonal_forest"]

    return BIOMES["temperate_forest"]


def compute_habitability(province: Province, sea_level: float = 0.4, infrastructure_level: float = 0.0) -> float:
    biome = classify_biome(province, sea_level)
    temp_score = 1 - abs(province.avg_temp - 0.52) * 1.45
    rain_score = 1 - abs(province.avg_rain - 0.55) * 1.15
    elevation_penalty = max(0.0, province.avg_elev - 0.82) * 0.55
    wind_penalty = max(0.0, province.wind_speed - 0.78) * 0.25
    infrastructure_bonus = min(0.18, infrastructure_level * 0.035)
    score = (biome.habitability * 0.5) + (temp_score * 0.22) + (rain_score * 0.18) + infrastructure_bonus
    return round(clamp(score - elevation_penalty - wind_penalty), 3)


def infer_primary_planet_type(provinces: list[Province], sea_level: float = 0.4, solar_constant: float = 1.0) -> str:
    if not provinces:
        return "Unsurveyed"

    biomes = [classify_biome(province, sea_level) for province in provinces]
    tags = Counter(tag for biome in biomes for tag in biome.tags)
    avg_temp = mean(province.avg_temp for province in provinces)
    ocean_share = tags["ocean"] / len(provinces)
    dry_share = tags["dry"] / len(provinces)
    life_share = tags["life"] / len(provinces)
    rocky_share = tags["rocky"] / len(provinces)

    if ocean_share > 0.58:
        return "Ocean World"
    if avg_temp < 0.26 or solar_constant < 0.72:
        return "Cryogenic World"
    if dry_share > 0.55 and avg_temp > 0.56:
        return "Arid World"
    if rocky_share > 0.48:
        return "Lithic World"
    if life_share > 0.55:
        return "Gaian World"
    if avg_temp > 0.76 or solar_constant > 1.28:
        return "Hothouse World"
    return "Mixed Terrestrial"


def infer_secondary_planet_trait(provinces: list[Province], sea_level: float = 0.4, coriolis_factor: float = 1.0) -> str:
    if not provinces:
        return "Uncharted"

    biomes = [classify_biome(province, sea_level) for province in provinces]
    tags = Counter(tag for biome in biomes for tag in biome.tags)
    avg_wind = mean(province.wind_speed for province in provinces)
    avg_elev = mean(province.avg_elev for province in provinces)
    avg_rain = mean(province.avg_rain for province in provinces)

    if avg_wind * coriolis_factor > 0.72:
        return "Storm-Banded"
    if tags["exotic"] >= max(1, len(provinces) // 4):
        return "Exotic Biosphere"
    if avg_elev > 0.64:
        return "Highland Crust"
    if avg_rain > 0.72:
        return "Monsoon Cycle"
    if tags["mineral"] + tags["rocky"] > len(provinces) * 0.45:
        return "Mineral Rich"
    if tags["life"] > len(provinces) * 0.5:
        return "Biogenic Soils"
    return "Stable Climate"
