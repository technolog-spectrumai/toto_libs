from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import math
import random

import numpy as np
from django.core.files.base import ContentFile
from django.db import transaction
from PIL import Image

from .biomes import PlanetConfig, Province as BiomeProvince, classify_biome, compute_habitability
from .models import Planet, Province


@dataclass(frozen=True)
class GeneratedProvince:
    name: str
    x: int
    y: int
    width: int
    height: int
    avg_elev: float
    avg_temp: float
    avg_rain: float
    wind_speed: float
    cell_count: int


@dataclass(frozen=True)
class ProvinceCandidate:
    score: float
    habitability: float
    x: int
    y: int
    width: int
    height: int
    avg_elev: float
    avg_temp: float
    avg_rain: float
    wind_speed: float
    biome_name: str


@dataclass
class PlanetData:
    cx: int = 0
    cy: int = 0
    radius: int = 0
    elev_map: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    temp_map: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    rain_map: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    wind_map: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    ice_map: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    provinces: list[GeneratedProvince] = field(default_factory=list)


def build_planet_config(planet: Planet) -> PlanetConfig:
    return PlanetConfig(
        size=planet.map_size,
        radius=planet.radius,
        grid_size=planet.grid_size,
        perlin_scale=planet.perlin_scale,
        perlin_octaves=planet.perlin_octaves,
        sea_level=planet.sea_level,
        seed=planet.climate_seed,
        wind_iterations=planet.wind_iterations,
        axial_tilt=planet.axial_tilt,
        coriolis_factor=planet.coriolis_factor,
        solar_constant=planet.solar_constant,
    )


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    # Pillow expects RGB tuples. Do not swap to BGR unless this renderer moves to OpenCV.
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def normalize(values: np.ndarray) -> np.ndarray:
    floor = float(np.min(values))
    ceiling = float(np.max(values))
    if math.isclose(floor, ceiling):
        return np.zeros_like(values)
    return (values - floor) / (ceiling - floor)


class PlanetGenerator:
    def __init__(self, config: PlanetConfig):
        seed = config.seed if config.seed is not None else random.randint(1, 999999)
        self.config = PlanetConfig(
            size=config.size,
            radius=config.radius,
            grid_size=config.grid_size,
            perlin_scale=config.perlin_scale,
            perlin_octaves=config.perlin_octaves,
            sea_level=config.sea_level,
            seed=seed,
            wind_iterations=config.wind_iterations,
            axial_tilt=config.axial_tilt,
            coriolis_factor=config.coriolis_factor,
            solar_constant=config.solar_constant,
        )
        self.rng = np.random.default_rng(seed)
        self.cx = self.config.size // 2
        self.cy = self.config.size // 2

    def create_planet(self, province_count: int) -> PlanetData:
        elev_map = self.generate_elevation_map()
        temp_map = self.generate_temperature_map(elev_map)
        temp_map, ice_map = self.simulate_ice_and_snow(elev_map, temp_map)
        wind_map = self.generate_wind_map(elev_map)
        rain_map = self.generate_rain_map(elev_map, wind_map, temp_map)
        data = PlanetData(self.cx, self.cy, self.config.radius, elev_map, temp_map, rain_map, wind_map, ice_map)
        data.provinces = self.generate_provinces(data, province_count)
        return data

    def planet_mask(self) -> np.ndarray:
        return np.ones((self.config.size, self.config.size), dtype=bool)

    def resized_noise(self, cells: int) -> np.ndarray:
        cells = max(2, int(cells))
        coarse = self.rng.random((cells, cells))
        image = Image.fromarray(np.uint8(coarse * 255))
        image = image.resize((self.config.size, self.config.size), Image.Resampling.BICUBIC)
        return np.asarray(image, dtype=np.float32) / 255.0

    def generate_elevation_map(self) -> np.ndarray:
        size = self.config.size
        base_cells = max(3, int(size * self.config.perlin_scale * 8))
        elev_map = np.zeros((size, size), dtype=np.float32)
        total_weight = 0.0

        for octave in range(max(1, self.config.perlin_octaves)):
            frequency = 2**octave
            weight = 0.52**octave
            elev_map += self.resized_noise(base_cells * frequency) * weight
            total_weight += weight

        elev_map = normalize(elev_map / total_weight)
        elev_map = normalize(elev_map)
        elev_map = np.clip(elev_map - (self.config.sea_level - 0.4), 0, 1)
        return elev_map

    def generate_temperature_map(self, elev_map: np.ndarray) -> np.ndarray:
        size = self.config.size
        y = np.arange(size, dtype=np.float32)
        latitude = np.abs(y - self.cy) / max(1, self.cy)
        lat_temp = np.repeat((1 - latitude)[:, None], size, axis=1)
        seasonal_band = math.sin(math.radians(self.config.axial_tilt)) * 0.08
        temp_map = (lat_temp + seasonal_band + self.resized_noise(18) * 0.08) * self.config.solar_constant
        temp_map -= np.clip(elev_map - self.config.sea_level, 0, 1) * 0.25
        return np.clip(temp_map, 0, 1)

    def simulate_ice_and_snow(
        self,
        elev_map: np.ndarray,
        temp_map: np.ndarray,
        iterations: int = 5,
        albedo_effect: float = 0.1,
    ) -> tuple[np.ndarray, np.ndarray]:
        ice_map = np.zeros_like(elev_map, dtype=np.float32)
        cooled_temp = temp_map.copy()
        tilt_factor = abs(self.config.axial_tilt) / 23.5
        ice_threshold = 0.18 + min(0.12, tilt_factor * 0.045)

        for _ in range(max(1, iterations)):
            polar_or_cold = cooled_temp < ice_threshold
            high_snowpack = (elev_map > 0.84) & (cooled_temp < ice_threshold + 0.18)
            new_ice = polar_or_cold | high_snowpack
            ice_map[new_ice] = 1.0
            cooled_temp = np.clip(cooled_temp - ice_map * (albedo_effect / max(1, iterations)), 0, 1)

        return cooled_temp, ice_map

    def generate_wind_map(self, elev_map: np.ndarray) -> np.ndarray:
        size = self.config.size
        y = np.arange(size, dtype=np.float32)
        latitude = np.abs(y - self.cy) / max(1, self.cy)
        lat_wind = np.repeat((0.35 + latitude * 0.5)[:, None], size, axis=1)
        relief_drag = np.clip(1.1 - elev_map * 0.45, 0.2, 1.1)
        return np.clip(lat_wind * relief_drag * self.config.coriolis_factor, 0, 1)

    def generate_rain_map(self, elev_map: np.ndarray, wind_map: np.ndarray, temp_map: np.ndarray) -> np.ndarray:
        ocean = elev_map < self.config.sea_level
        land = ~ocean
        evaporation = ocean.astype(np.float32) * (0.025 + temp_map * 0.09) * (0.75 + self.config.sea_level * 0.5)
        moisture = evaporation + self.resized_noise(14) * 0.025
        rain_map = np.zeros_like(elev_map, dtype=np.float32)
        shift = 1 if self.config.coriolis_factor >= 0 else -1
        size = self.config.size
        latitude = np.abs(np.arange(size, dtype=np.float32) - self.cy) / max(1, self.cy)
        equatorial_lift = np.repeat((1 - latitude)[:, None], size, axis=1)

        for _ in range(max(1, self.config.wind_iterations) * 6):
            zonal = np.roll(moisture, shift, axis=1)
            meridional = (np.roll(moisture, 1, axis=0) + np.roll(moisture, -1, axis=0)) * 0.5
            advected = (
                moisture * 0.45
                + zonal * (0.35 + wind_map * 0.2)
                + meridional * 0.2
            ) / (1.0 + wind_map * 0.2)
            uplift = np.clip(elev_map - np.roll(elev_map, shift, axis=1), 0, 1)
            convection = np.clip(temp_map - 0.55, 0, 1) * equatorial_lift * 0.045
            condensation = np.clip(0.025 + land.astype(np.float32) * 0.035 + uplift * 0.16 + convection, 0.02, 0.28)
            rain = advected * condensation
            rain_map += rain
            moisture = np.clip(advected - rain + evaporation, 0, None)

        rain_ceiling = float(np.percentile(rain_map, 95))
        if math.isclose(rain_ceiling, 0):
            return rain_map
        moisture_budget = np.clip(
            0.34 + self.config.sea_level * 0.56 + self.config.wind_iterations * 0.012,
            0.22,
            1.0,
        )
        rain_map = np.clip((rain_map / rain_ceiling) * moisture_budget, 0, 1)
        return rain_map

    def render_satellite_planet(self, data: PlanetData) -> bytes:
        size = self.config.size
        image = Image.new("RGB", (size, size), (0, 0, 0))
        pixels = image.load()

        for y in range(size):
            for x in range(size):
                biome = classify_biome(
                    BiomeProvince(
                        x=x,
                        y=y,
                        width=1,
                        height=1,
                        avg_elev=float(data.elev_map[y, x]),
                        avg_temp=float(data.temp_map[y, x]),
                        avg_rain=float(data.rain_map[y, x]),
                        wind_speed=float(data.wind_map[y, x]),
                    ),
                    self.config.sea_level,
                )
                color = np.asarray(hex_to_rgb(biome.color), dtype=np.float32)
                if data.ice_map[y, x] > 0:
                    ice_color = np.asarray((226, 236, 238), dtype=np.float32)
                    color = color * 0.25 + ice_color * 0.75
                pixels[x, y] = tuple(int(part) for part in np.clip(color, 0, 255))

        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()

    def light_map(self, size: int) -> np.ndarray:
        y, x = np.ogrid[:size, :size]
        nx = (x - self.cx) / max(1, self.config.radius)
        ny = (y - self.cy) / max(1, self.config.radius)
        radial = np.clip(1 - np.sqrt(nx**2 + ny**2) * 0.18, 0.65, 1.0)
        terminator = np.clip(0.86 + (nx * -0.18) + (ny * -0.08), 0.62, 1.08)
        return radial * terminator

    def generate_provinces(self, data: PlanetData, province_count: int) -> list[GeneratedProvince]:
        province_count = max(1, province_count)
        tile = max(10, int(self.config.size / max(2, self.config.grid_size)))
        candidates = []

        for top in range(0, self.config.size, tile):
            for left in range(0, self.config.size, tile):
                y0 = top
                x0 = left
                y1 = min(self.config.size, top + tile)
                x1 = min(self.config.size, left + tile)
                if y1 <= y0 or x1 <= x0:
                    continue

                elev = float(np.mean(data.elev_map[y0:y1, x0:x1]))
                temp = float(np.mean(data.temp_map[y0:y1, x0:x1]))
                rain = float(np.mean(data.rain_map[y0:y1, x0:x1]))
                wind = float(np.mean(data.wind_map[y0:y1, x0:x1]))
                biome_province = BiomeProvince(x0, y0, x1 - x0, y1 - y0, elev, temp, rain, wind_speed=wind)
                biome = classify_biome(biome_province, self.config.sea_level)
                habitability = compute_habitability(biome_province, self.config.sea_level)
                land_bonus = 0.18 if elev >= self.config.sea_level else -0.12
                center_x = x0 + (x1 - x0) / 2
                center_y = y0 + (y1 - y0) / 2
                center_bias = 1 - (math.dist((center_x, center_y), (self.cx, self.cy)) / max(1, self.config.size)) * 0.15
                habitability_score = habitability * 0.7
                score = habitability_score + land_bonus + center_bias + float(self.rng.random()) * 0.08
                candidates.append(
                    ProvinceCandidate(
                        score=score,
                        habitability=habitability,
                        x=int(center_x),
                        y=int(center_y),
                        width=x1 - x0,
                        height=y1 - y0,
                        avg_elev=elev,
                        avg_temp=temp,
                        avg_rain=rain,
                        wind_speed=wind,
                        biome_name=biome.name,
                    )
                )

        selected = self.select_evenly_spread_candidates(candidates, province_count)
        return [self.build_province(index, row) for index, row in enumerate(selected, start=1)]

    def select_evenly_spread_candidates(self, candidates: list[ProvinceCandidate], province_count: int) -> list[ProvinceCandidate]:
        selected: list[ProvinceCandidate] = []
        remaining = sorted(candidates, key=lambda candidate: candidate.score, reverse=True)
        min_distance = self.config.size / max(2, province_count**0.5)

        while remaining and len(selected) < province_count:
            best = max(remaining, key=lambda candidate: self.spread_score(candidate, selected, min_distance))
            selected.append(best)
            remaining.remove(best)

        return sorted(selected, key=lambda candidate: (candidate.y, candidate.x))

    def spread_score(self, candidate: ProvinceCandidate, selected: list[ProvinceCandidate], min_distance: float) -> float:
        if not selected:
            return candidate.score
        nearest = min(math.dist((candidate.x, candidate.y), (row.x, row.y)) for row in selected)
        spacing = min(1.0, nearest / max(1.0, min_distance))
        crowding_penalty = max(0.0, (min_distance - nearest) / max(1.0, min_distance)) * 0.7
        return candidate.score + spacing * 0.35 - crowding_penalty

    def build_province(self, index: int, row: ProvinceCandidate) -> GeneratedProvince:
        x = row.x
        y = row.y
        width = row.width
        height = row.height
        province_area = max(1, width * height)
        cell_count = max(6, min(32, int(province_area / 95)))
        return GeneratedProvince(
            name=f"{row.biome_name} {index}",
            x=int(x),
            y=int(y),
            width=int(width),
            height=int(height),
            avg_elev=round(row.avg_elev, 4),
            avg_temp=round(row.avg_temp, 4),
            avg_rain=round(row.avg_rain, 4),
            wind_speed=round(row.wind_speed, 4),
            cell_count=cell_count,
        )


def update_planet_provinces(planet: Planet, generated: list[GeneratedProvince]) -> None:
    existing = list(planet.provinces.order_by("id"))
    for index, province in enumerate(existing):
        province.name = f"regenerating-{planet.id}-{index}"
        province.save(update_fields=["name"])

    for index, spec in enumerate(generated):
        defaults = {
            "name": spec.name,
            "x": spec.x,
            "y": spec.y,
            "width": spec.width,
            "height": spec.height,
            "avg_elev": spec.avg_elev,
            "avg_temp": spec.avg_temp,
            "avg_rain": spec.avg_rain,
            "wind_speed": spec.wind_speed,
            "cell_count": spec.cell_count,
        }
        if index < len(existing):
            for field, value in defaults.items():
                setattr(existing[index], field, value)
            existing[index].save(update_fields=list(defaults))
        else:
            Province.objects.create(planet=planet, **defaults)

    for province in existing[len(generated) :]:
        province.delete()


@transaction.atomic
def generate_planet_map(planet: Planet) -> PlanetData:
    generator = PlanetGenerator(build_planet_config(planet))
    data = generator.create_planet(planet.max_provinces)
    image_bytes = generator.render_satellite_planet(data)
    update_planet_provinces(planet, data.provinces)

    if planet.climate_seed != generator.config.seed:
        planet.climate_seed = generator.config.seed
    filename = f"planet-{planet.id or 'new'}-{generator.config.seed}.png"
    planet.map_image.save(filename, ContentFile(image_bytes), save=False)
    planet.save(update_fields=["climate_seed", "map_image"])
    return data
