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
    "abyssal_ocean": BiomeClass("abyssal_ocean", "Abyssal Ocean", "#0f3f66", 0.12, 0.8, 0.65, ("ocean", "wet")),
    "kelp_shelf": BiomeClass("kelp_shelf", "Kelp Shelf", "#0e7490", 0.42, 0.9, 0.8, ("ocean", "wet", "life")),
    "tidal_marsh": BiomeClass("tidal_marsh", "Tidal Marsh", "#2f855a", 0.58, 0.95, 0.92, ("wet", "life")),
    "monsoon_jungle": BiomeClass("monsoon_jungle", "Monsoon Jungle", "#15803d", 0.72, 0.85, 0.95, ("wet", "hot", "life")),
    "cloud_forest": BiomeClass("cloud_forest", "Cloud Forest", "#16a34a", 0.78, 0.9, 1.0, ("wet", "life", "highland")),
    "temperate_mosaic": BiomeClass("temperate_mosaic", "Temperate Mosaic", "#22c55e", 0.86, 1.0, 1.05, ("temperate", "life")),
    "savanna_arc": BiomeClass("savanna_arc", "Savanna Arc", "#84cc16", 0.66, 1.05, 1.0, ("dry", "life")),
    "mycelial_fen": BiomeClass("mycelial_fen", "Mycelial Fen", "#65a30d", 0.7, 1.1, 0.95, ("wet", "life", "exotic")),
    "basalt_badlands": BiomeClass("basalt_badlands", "Basalt Badlands", "#57534e", 0.28, 1.35, 0.85, ("dry", "rocky")),
    "glass_desert": BiomeClass("glass_desert", "Glass Desert", "#facc15", 0.18, 1.15, 0.65, ("dry", "hot")),
    "salt_pan": BiomeClass("salt_pan", "Salt Pan", "#e5e7eb", 0.24, 1.05, 0.75, ("dry", "mineral")),
    "cryo_steppe": BiomeClass("cryo_steppe", "Cryo Steppe", "#67e8f9", 0.32, 1.0, 0.75, ("cold", "dry")),
    "boreal_taiga": BiomeClass("boreal_taiga", "Boreal Taiga", "#166534", 0.62, 0.95, 0.9, ("cold", "life")),
    "ash_tundra": BiomeClass("ash_tundra", "Ash Tundra", "#94a3b8", 0.2, 1.2, 0.7, ("cold", "rocky")),
    "alpine_scree": BiomeClass("alpine_scree", "Alpine Scree", "#a8a29e", 0.36, 1.25, 0.8, ("highland", "rocky")),
    "geothermal_oasis": BiomeClass("geothermal_oasis", "Geothermal Oasis", "#f97316", 0.64, 1.2, 1.05, ("hot", "life", "exotic")),
    "storm_scrub": BiomeClass("storm_scrub", "Storm Scrub", "#38bdf8", 0.54, 1.0, 0.88, ("windy", "life")),
    "unknown": BiomeClass("unknown", "Unknown", "#64748b", 0.4),
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

    if elev < sea_level - 0.18:
        return BIOMES["abyssal_ocean"]
    if elev < sea_level:
        return BIOMES["kelp_shelf"] if temp > 0.28 else BIOMES["cryo_steppe"]
    if elev < sea_level + 0.05 and rain > 0.68:
        return BIOMES["tidal_marsh"]
    if temp > 0.78 and rain < 0.18:
        return BIOMES["glass_desert"]
    if rain < 0.16 and elev < 0.48:
        return BIOMES["salt_pan"]
    if temp < 0.18 and rain < 0.35:
        return BIOMES["cryo_steppe"]
    if temp < 0.25 and elev > 0.62:
        return BIOMES["ash_tundra"]
    if elev > 0.76:
        return BIOMES["alpine_scree"] if rain < 0.62 else BIOMES["cloud_forest"]
    if temp > 0.72 and rain > 0.72:
        return BIOMES["monsoon_jungle"]
    if rain > 0.82 and temp < 0.58:
        return BIOMES["mycelial_fen"]
    if temp < 0.42 and rain > 0.42:
        return BIOMES["boreal_taiga"]
    if rain < 0.32 and elev > 0.52:
        return BIOMES["basalt_badlands"]
    if wind > 0.72 and rain > 0.35:
        return BIOMES["storm_scrub"]
    if rain < 0.45:
        return BIOMES["savanna_arc"]
    return BIOMES["temperate_mosaic"]


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
