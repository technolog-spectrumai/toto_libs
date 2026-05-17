import numpy as np
import cv2
import matplotlib.pyplot as plt
from noise import pnoise2
from tqdm import tqdm
from dataclasses import dataclass, field
import random

# -----------------------------
# DATACLASSES
# -----------------------------
@dataclass
class Province:
    x: int
    y: int
    width: int
    height: int
    avg_elev: float = 0
    avg_temp: float = 0
    avg_rain: float = 0
    biome: str = "Unknown"
    biome_color: tuple = (0,0,0)  # BGR
    wind_speed: float = 0

@dataclass
class PlanetConfig:
    size: int = 512             # canvas size (512x512)
    radius: int = 200           # circular planet radius in pixels
    grid_size: int = 12         # number of provinces per radius
    perlin_scale: float = 0.003 # smaller scale → larger continents
    perlin_octaves: int = 8     # fewer octaves → smooth continents
    sea_level: float = 0.4      # 0–1, default ocean coverage
    seed: int = None            # random seed (None → random)
    wind_iterations: int = 5    # steps for moisture/wind simulation
    axial_tilt: float = 23.5    # degrees, like Earth
    coriolis_factor: float = 1.0 # relative to Earth rotation
    solar_constant: float = 1.0 # relative to Earth (1.0 = Earth-like, <1 colder, >1 hotter)

@dataclass
class PlanetData:
    cx: int = 0
    cy: int = 0
    radius: int = 0
    elev_map: np.ndarray = field(default_factory=lambda: np.zeros((0,0)))
    temp_map: np.ndarray = field(default_factory=lambda: np.zeros((0,0)))
    rain_map: np.ndarray = field(default_factory=lambda: np.zeros((0,0)))
    wind_map: np.ndarray = field(default_factory=lambda: np.zeros((0,0)))
    provinces: list = field(default_factory=list)
    image_path: str = ""

# -----------------------------
# BIOME COLORS
# -----------------------------
BIOME_COLORS = {
    "Ocean": (255,0,0),
    "Snow": (200,200,200),
    "Tundra": (255,255,255),
    "Desert": (0,255,255),
    "Rainforest": (0,128,0),
    "Savanna": (0,255,0),
    "Grassland": (0,200,0),
}

class PlanetGenerator:
    def __init__(self, config: PlanetConfig):
        self.config = config
        self.cx = config.size // 2
        self.cy = config.size // 2
        if self.config.seed is None:
            self.config.seed = random.randint(0,10000)

    # -----------------------------
    # MAP GENERATION
    # -----------------------------
    def generate_noise_map(self):
        elev_map = np.zeros((self.config.size, self.config.size))
        for i in tqdm(range(self.config.size), desc="Generating Elevation Map"):
            for j in range(self.config.size):
                nx, ny = i * self.config.perlin_scale, j * self.config.perlin_scale
                elev = pnoise2(nx, ny, octaves=self.config.perlin_octaves, base=self.config.seed)
                elev_map[i,j] = elev
        # normalize 0-1
        elev_map = (elev_map - np.min(elev_map)) / (np.max(elev_map) - np.min(elev_map))
        
        # apply sea_level offset to expand/reduce ocean coverage
        elev_map = elev_map - (self.config.sea_level - 0.4)  # 0.4 = default
        elev_map = np.clip(elev_map, 0, 1)
        
        return elev_map

    def generate_temp_map(self):
        size = self.config.size
        lat_map = np.array([[abs(j - self.cy)/self.cy for j in range(size)] for i in range(size)])
        base_temp = 1 - lat_map  # 1 at equator, 0 at poles
        noise = np.random.rand(size, size) * 0.1
        temp_map = (base_temp + noise) * self.config.solar_constant
        temp_map = np.clip(temp_map, 0, 1)  # keep in 0-1 range
        return temp_map

    def generate_rain_map(self):
        return np.zeros((self.config.size,self.config.size))

    # -----------------------------
    # WIND & MOISTURE FUNCTIONS
    # -----------------------------
    def generate_wind_map(self, planet_data: PlanetData):
        size = self.config.size
        wind_map = np.zeros((size, size))
        for i in range(size):
            for j in range(size):
                elev = planet_data.elev_map[i,j]
                wind_base = 1.0 if elev < self.config.sea_level else max(0.2, 1.0 - elev*0.5)
                # Apply Coriolis effect
                wind_map[i,j] = wind_base * self.config.coriolis_factor
        planet_data.wind_map = wind_map
        return wind_map

    def simulate_moisture_transport(self, planet_data: PlanetData, iterations=None):
        if iterations is None:
            iterations = self.config.wind_iterations
        size = self.config.size
        wind_map = planet_data.wind_map
        rain_map = np.zeros((size, size))

        for step in range(iterations):
            new_rain = rain_map.copy()
            for i in range(size):
                for j in range(size):
                    moisture = 0
                    for ni,nj in [(i-1,j),(i+1,j),(i,j-1),(i,j+1)]:
                        if 0<=ni<size and 0<=nj<size:
                            if planet_data.elev_map[ni,nj] < self.config.sea_level:
                                moisture += 0.2
                            else:
                                moisture += 0.05
                    new_rain[i,j] += moisture * wind_map[i,j] * 0.1
            rain_map = new_rain

        # Normalize
        rain_map = rain_map / np.max(rain_map)
        planet_data.rain_map = rain_map
        return rain_map

    def simulate_monsoons(self, planet_data: PlanetData, iterations=None):
        if iterations is None:
            iterations = self.config.wind_iterations
        size = self.config.size
        wind_map = planet_data.wind_map
        rain_map = planet_data.rain_map.copy()
        tilt_factor = self.config.axial_tilt / 23.5
        
        for step in range(iterations):
            new_rain = rain_map.copy()
            for i in range(size):
                for j in range(size):
                    # Neighbor advection
                    moisture = 0
                    for ni,nj in [(i-1,j),(i+1,j),(i,j-1),(i,j+1)]:
                        if 0<=ni<size and 0<=nj<size:
                            if planet_data.elev_map[ni,nj] < self.config.sea_level:
                                moisture += 0.2
                            else:
                                moisture += 0.05
    
                    # Latitude factor: strongest monsoon near equator
                    lat_factor = abs(i - size//2) / (size/2)
                    monsoon_strength = max(0, 1 - lat_factor*1.5)
                    monsoon_strength *= tilt_factor
    
                    # Seasonal multiplier
                    seasonal = 0.5 + 0.5 * np.sin(np.pi * step / iterations)
    
                    # Update rain map
                    new_rain[i,j] += moisture * wind_map[i,j] * 0.05 * seasonal * monsoon_strength
    
            rain_map = new_rain
    
        # Normalize final rainfall
        rain_map = rain_map / np.max(rain_map)
        planet_data.rain_map = rain_map
        return rain_map

    def simulate_ice_and_snow(self, planet_data: PlanetData, iterations=5, albedo_effect=0.1):
        """
        Iterative ice-age style simulation:
        - Cold regions form ice
        - Ice increases albedo → lowers temperature locally
        - More ice forms in feedback loop
        """
        size = self.config.size
        elev_map = planet_data.elev_map.copy()
        temp_map = planet_data.temp_map.copy()
    
        ice_map = np.zeros((size, size))  # 0=none, 1=ice
        tilt_factor = self.config.axial_tilt / 23.5
        
        for step in range(iterations):
            # 1. Determine where ice forms (cold or high elevation)
            ice_threshold = 0.2  # temp threshold
            
            adjusted_threshold = ice_threshold * (1 + tilt_factor)
            new_ice = (temp_map < adjusted_threshold) | (elev_map > 0.85)
            ice_map[new_ice] = 1
    
            # 2. Ice increases albedo → local cooling
            temp_map[ice_map == 1] -= albedo_effect / iterations
            temp_map = np.clip(temp_map, 0, 1)
    
        # Apply ice to elevation map for visualization
        planet_data.elev_map = np.maximum(elev_map, ice_map * 0.95)

    def generate_wind_and_rain(self, planet_data: PlanetData):
        self.generate_wind_map(planet_data)
        self.simulate_moisture_transport(planet_data)
        self.simulate_monsoons(planet_data)
        self.simulate_ice_and_snow(planet_data)

    # -----------------------------
    # CREATE PLANET
    # -----------------------------
    def create_planet(self):
        elev_map = self.generate_noise_map()
        temp_map = self.generate_temp_map()
        rain_map = self.generate_rain_map()
        planet_data = PlanetData(self.cx, self.cy, self.config.radius, elev_map, temp_map, rain_map)
        self.generate_wind_and_rain(planet_data)
        return planet_data

    # -----------------------------
    # SATELLITE MAP RENDER
    # -----------------------------
    def render_satellite_planet(self, planet_data: PlanetData):
        size = self.config.size
        canvas = np.zeros((size, size, 3), dtype=np.uint8) + 20

        for i in tqdm(range(size), desc="Rendering Satellite Map"):
            for j in range(size):
                elev = planet_data.elev_map[i, j]
                temp = planet_data.temp_map[i, j]
                rain = planet_data.rain_map[i, j]
                canvas[i, j] = satellite_color(elev, temp, rain, self.config.sea_level)

        return canvasdef satellite_color(elev, temp, rain, y=None, height=None, sea_level=0.4):
    """
    Realistic Earth-style satellite colors with exaggerated biome shades:
    - Ocean: deep blue
    - Ice/snow: white if high elevation, polar, or very cold
    - Mountains: gray
    - Tundra: grayish
    - Boreal forest: greenish (cold & wet)
    - Mixed temperate forest: forest green (moderate temp & rain)
    - Jungle / rainforest: turquoise-green (hot & wet)
    - Deserts: hot (orange) and cold (pale yellow/gray)
    - Savanna: yellow-green (warm & moderate rain)
    - Steppe / grassland: light green (moderate temp & low rain)
    """

    # Ocean
    if elev < sea_level:
        return (0, 0, 180)

    land_h = (elev - sea_level) / (1 - sea_level)

    # Polar detection
    polar = False
    if y is not None and height is not None:
        polar_band = int(height * 0.1)
        if y < polar_band or y >= height - polar_band:
            polar = True

    # Snow / ice: high elevation, polar, or very cold
    if land_h > 0.85 or polar or temp < 0.15:
        return (240, 240, 240)

    # Mountains
    if land_h > 0.6:
        gray = int(140 + 60 * land_h)
        return (gray, gray, gray)

    # Boreal forest: cold & wet → slightly blue-green
    if temp < 0.5 and rain > 0.5:
        return (0, 100, 40)

    # Tundra (cold & dry)
    if temp < 0.3:
        return (150, 150, 120)

    # Mixed temperate forest / Europe-like climate
    if 0.4 <= temp <= 0.7 and 0.4 <= rain <= 0.7:
        return (0, 160, 60)

    # Jungle / rainforest (hot & wet) → turquoise-green
    if rain > 0.7 and temp > 0.6:
        return (0, 150, 100)

    # Savanna (warm & moderate rain)
    if 0.3 < rain <= 0.7 and temp > 0.6:
        return (180, 200, 50)

    # Steppe / grassland (moderate temp & low rain) → yellow-green shade
    if 0.3 < temp <= 0.6 and rain <= 0.3:
        return (200, 210, 100)

    # Deserts
    if rain < 0.2:
        if temp > 0.6:  # hot desert
            return (230, 200, 50)
        else:           # cold desert (like Gobi)
            return (210, 210, 180)

    # Default: temperate grassland
    return (120, 180, 60)import numpy as np
import matplotlib.pyplot as plt

def draw_geophysical_maps(planet_data):
    # Rotate all maps 90° clockwise so north is up
    elev_rot = np.rot90(planet_data.elev_map, k=-1)
    temp_rot = np.rot90(planet_data.temp_map, k=-1)
    rain_rot = np.rot90(planet_data.rain_map, k=-1)
    wind_rot = np.rot90(planet_data.wind_map, k=-1)
    
    # Plot
    fig, axes = plt.subplots(2, 2, figsize=(12, 12))

    im0 = axes[0, 0].imshow(elev_rot, cmap='terrain')
    axes[0, 0].set_title("Elevation")
    fig.colorbar(im0, ax=axes[0, 0], fraction=0.046, pad=0.04)

    im1 = axes[0, 1].imshow(temp_rot, cmap='coolwarm')
    axes[0, 1].set_title("Temperature")
    fig.colorbar(im1, ax=axes[0, 1], fraction=0.046, pad=0.04)

    im2 = axes[1, 0].imshow(rain_rot, cmap='Blues')
    axes[1, 0].set_title("Rainfall / Moisture Transport")
    fig.colorbar(im2, ax=axes[1, 0], fraction=0.046, pad=0.04)

    im3 = axes[1, 1].imshow(wind_rot, cmap='cool')
    axes[1, 1].set_title("Wind Speed")
    fig.colorbar(im3, ax=axes[1, 1], fraction=0.046, pad=0.04)

    # Turn off axes
    for ax in axes.flatten():
        ax.axis('off')

    plt.tight_layout()
    plt.show()
