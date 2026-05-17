from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .biomes import (
    Province as BiomeProvince,
    classify_biome,
    compute_habitability,
    infer_primary_planet_type,
    infer_secondary_planet_trait,
)
from .engine import level_multiplier


class ItemTier(models.IntegerChoices):
    RESOURCE = 1, "Resource"
    COMPONENT = 2, "Component"


class CargoClass(models.TextChoices):
    BULK = "bulk", "Bulk"
    LIQUID = "liquid", "Liquid"
    ENERGY = "energy", "Energy"
    HIGH_VALUE = "high_value", "High Value"
    HAZARDOUS = "hazardous", "Hazardous"
    PEOPLE = "people", "People"
    ORBITAL = "orbital", "Orbital"


class BuildingCategory(models.TextChoices):
    EXTRACTION = "extraction", "Extraction"
    ENERGY = "energy", "Energy"
    INFRASTRUCTURE = "infrastructure", "Infrastructure"
    INDUSTRY = "industry", "Industry"
    HIGH_TECH = "high_tech", "High-Tech Industry"
    FOOD = "food", "Food"
    LOGISTICS = "logistics", "Logistics"
    POPULATION = "population", "Population"
    RESEARCH = "research", "Research"
    ORBITAL = "orbital", "Orbital"


class BuildingStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    CONSTRUCTING = "constructing", "Constructing"
    DISABLED = "disabled", "Disabled"


class BuildingPriority(models.TextChoices):
    CRITICAL = "critical", "Critical"
    HIGH = "high", "High"
    NORMAL = "normal", "Normal"
    LOW = "low", "Low"


class Empire(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="game_empire")
    name = models.CharField(max_length=80)
    credits = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("1000.00"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ItemType(models.Model):
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=80)
    tier = models.PositiveSmallIntegerField(choices=ItemTier.choices)
    cargo_class = models.CharField(max_length=30, choices=CargoClass.choices, default=CargoClass.BULK)
    is_energy = models.BooleanField(default=False)
    is_strategic = models.BooleanField(default=False)
    base_market_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("1.00"))
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["tier", "name"]

    def __str__(self):
        return self.name


class Planet(models.Model):
    owner = models.ForeignKey(Empire, null=True, blank=True, related_name="planets", on_delete=models.SET_NULL)
    name = models.CharField(max_length=80)
    gravity = models.FloatField(default=1.0)
    radiation = models.FloatField(default=0.0)
    max_provinces = models.PositiveIntegerField(default=5)
    map_size = models.PositiveIntegerField(default=512)
    radius = models.PositiveIntegerField(default=200)
    grid_size = models.PositiveIntegerField(default=12)
    perlin_scale = models.FloatField(default=0.003)
    perlin_octaves = models.PositiveIntegerField(default=8)
    sea_level = models.FloatField(default=0.4)
    climate_seed = models.IntegerField(null=True, blank=True)
    wind_iterations = models.PositiveIntegerField(default=5)
    axial_tilt = models.FloatField(default=23.5)
    coriolis_factor = models.FloatField(default=1.0)
    solar_constant = models.FloatField(default=1.0)
    map_image = models.ImageField(upload_to="game/planet_maps/", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def province_count(self):
        return self.provinces.count()

    @property
    def biome_provinces(self):
        return [province.to_biome_province() for province in self.provinces.all()]

    @property
    def primary_type(self):
        return infer_primary_planet_type(self.biome_provinces, self.sea_level, self.solar_constant)

    @property
    def secondary_trait(self):
        return infer_secondary_planet_trait(self.biome_provinces, self.sea_level, self.coriolis_factor)

    @property
    def total_area(self):
        return sum(province.area for province in self.provinces.all())

    @property
    def size(self):
        return self.total_area

    @property
    def habitability(self):
        provinces = list(self.provinces.all())
        if not provinces:
            return 0
        return sum(province.habitability for province in provinces) / len(provinces)


class Province(models.Model):
    planet = models.ForeignKey(Planet, related_name="provinces", on_delete=models.CASCADE)
    name = models.CharField(max_length=80)
    width = models.PositiveIntegerField(default=1)
    height = models.PositiveIntegerField(default=1)
    avg_elev = models.FloatField(default=0.0)
    avg_temp = models.FloatField(default=0.5)
    avg_rain = models.FloatField(default=0.5)
    wind_speed = models.FloatField(default=0.0)
    cell_count = models.PositiveIntegerField(default=10)
    x = models.IntegerField(default=0)
    y = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["planet", "name"]
        unique_together = ("planet", "name")

    def __str__(self):
        return f"{self.name} / {self.planet.name}"

    @property
    def used_cells(self):
        return sum(building.building_type.cell_cost for building in self.buildings.all())

    @property
    def free_cells(self):
        return max(0, self.cell_count - self.used_cells)

    @property
    def area(self):
        return self.width * self.height

    def to_biome_province(self):
        return BiomeProvince(
            x=self.x,
            y=self.y,
            width=self.width,
            height=self.height,
            avg_elev=self.avg_elev,
            avg_temp=self.avg_temp,
            avg_rain=self.avg_rain,
            wind_speed=self.wind_speed,
        )

    @property
    def biome_class(self):
        return classify_biome(self.to_biome_province(), self.planet.sea_level if self.planet_id else 0.4)

    @property
    def biome(self):
        return self.biome_class.name

    @property
    def biome_code(self):
        return self.biome_class.code

    @property
    def biome_color(self):
        return self.biome_class.color

    @property
    def infrastructure_level(self):
        total = 0.0
        for building in self.buildings.select_related("building_type").all():
            if building.status == BuildingStatus.ACTIVE and building.building_type.category == BuildingCategory.INFRASTRUCTURE:
                total += building.level * building.condition * 0.5
        return round(total, 3)

    @property
    def habitability(self):
        return compute_habitability(
            self.to_biome_province(),
            self.planet.sea_level if self.planet_id else 0.4,
            self.infrastructure_level,
        )

    def clean(self):
        if self.planet_id and self.pk is None and self.planet.province_count >= self.planet.max_provinces:
            raise ValidationError("This planet already has the maximum number of provinces.")


class DepositType(models.Model):
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=80)
    produces_item = models.ForeignKey(ItemType, on_delete=models.PROTECT)
    is_renewable = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProvinceDeposit(models.Model):
    province = models.ForeignKey(Province, related_name="deposits", on_delete=models.CASCADE)
    deposit_type = models.ForeignKey(DepositType, on_delete=models.PROTECT)
    abundance = models.FloatField(default=1.0)
    richness = models.FloatField(default=1.0)
    accessibility = models.FloatField(default=1.0)
    depletion = models.FloatField(default=0.0)

    class Meta:
        unique_together = ("province", "deposit_type")
        ordering = ["province", "deposit_type"]

    def __str__(self):
        return f"{self.deposit_type.name} in {self.province.name}"

    @property
    def extraction_factor(self):
        return max(0.0, self.abundance * self.richness * self.accessibility * (1.0 - self.depletion))


class ProvinceInventory(models.Model):
    province = models.ForeignKey(Province, related_name="inventory", on_delete=models.CASCADE)
    item_type = models.ForeignKey(ItemType, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=24, decimal_places=4, default=Decimal("0.0000"))
    capacity = models.DecimalField(max_digits=24, decimal_places=4, default=Decimal("100000.0000"))

    class Meta:
        unique_together = ("province", "item_type")
        ordering = ["province", "item_type"]

    def __str__(self):
        return f"{self.quantity} {self.item_type.name} in {self.province.name}"

    @property
    def free_capacity(self):
        return max(Decimal("0.0000"), self.capacity - self.quantity)


class ProvincePopulation(models.Model):
    province = models.OneToOneField(Province, related_name="population", on_delete=models.CASCADE)
    total_population = models.PositiveIntegerField(default=0)
    laborers = models.PositiveIntegerField(default=0)
    technicians = models.PositiveIntegerField(default=0)
    engineers = models.PositiveIntegerField(default=0)
    scientists = models.PositiveIntegerField(default=0)
    administrators = models.PositiveIntegerField(default=0)
    happiness = models.FloatField(default=0.7)
    health = models.FloatField(default=0.7)
    education = models.FloatField(default=0.3)

    def __str__(self):
        return f"Population of {self.province.name}"


class BuildingType(models.Model):
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=40, choices=BuildingCategory.choices, default=BuildingCategory.INDUSTRY)
    cell_cost = models.PositiveIntegerField(default=1)
    base_energy_demand = models.FloatField(default=0.0)
    max_level = models.PositiveIntegerField(default=10)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "name"]

    def __str__(self):
        return self.name


class Building(models.Model):
    province = models.ForeignKey(Province, related_name="buildings", on_delete=models.CASCADE)
    building_type = models.ForeignKey(BuildingType, on_delete=models.PROTECT)
    level = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=30, choices=BuildingStatus.choices, default=BuildingStatus.ACTIVE)
    priority = models.CharField(max_length=20, choices=BuildingPriority.choices, default=BuildingPriority.NORMAL)
    efficiency = models.FloatField(default=1.0)
    condition = models.FloatField(default=1.0)
    selected_recipe = models.ForeignKey("Recipe", null=True, blank=True, on_delete=models.SET_NULL, related_name="selected_by_buildings")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["province", "building_type"]

    def __str__(self):
        return f"{self.building_type.name} in {self.province.name}"

    @property
    def level_multiplier(self):
        return level_multiplier(self.level)

    def clean(self):
        if self.level > self.building_type.max_level:
            raise ValidationError("Building level exceeds max level.")
        # TODO: Prevent creation if province.free_cells is insufficient without counting this instance.


class Recipe(models.Model):
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    building_type = models.ForeignKey(BuildingType, related_name="recipes", on_delete=models.CASCADE)
    output_item = models.ForeignKey(ItemType, related_name="produced_by_recipes", on_delete=models.PROTECT)
    output_quantity_per_day = models.FloatField(default=1.0)
    is_default = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["building_type", "name"]

    def __str__(self):
        return self.name


class RecipeInput(models.Model):
    recipe = models.ForeignKey(Recipe, related_name="inputs", on_delete=models.CASCADE)
    item_type = models.ForeignKey(ItemType, on_delete=models.PROTECT)
    quantity_per_day = models.FloatField(default=1.0)

    class Meta:
        unique_together = ("recipe", "item_type")
        ordering = ["recipe", "item_type"]

    def __str__(self):
        return f"{self.quantity_per_day}/day {self.item_type.name} for {self.recipe.name}"


class ConstructionProject(models.Model):
    province = models.ForeignKey(Province, related_name="construction_projects", on_delete=models.CASCADE)
    building_type = models.ForeignKey(BuildingType, on_delete=models.PROTECT)
    target_level = models.PositiveIntegerField(default=1)
    progress = models.FloatField(default=0.0)
    required_work = models.FloatField(default=100.0)
    priority = models.CharField(max_length=20, choices=BuildingPriority.choices, default=BuildingPriority.NORMAL)
    status = models.CharField(max_length=30, choices=BuildingStatus.choices, default=BuildingStatus.CONSTRUCTING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["province", "-created_at"]

    def __str__(self):
        return f"Build {self.building_type.name} in {self.province.name}"

    @property
    def progress_percent(self):
        if self.required_work <= 0:
            return 100
        return min(100, round((self.progress / self.required_work) * 100, 1))


class TransportMode(models.TextChoices):
    ROAD = "road", "Road"
    RAIL = "rail", "Rail"
    PIPELINE = "pipeline", "Pipeline"
    GRID = "grid", "Power Grid"


class TransportLink(models.Model):
    from_province = models.ForeignKey(Province, related_name="links_out", on_delete=models.CASCADE)
    to_province = models.ForeignKey(Province, related_name="links_in", on_delete=models.CASCADE)
    mode = models.CharField(max_length=30, choices=TransportMode.choices, default=TransportMode.ROAD)
    capacity_per_day = models.FloatField(default=100.0)
    condition = models.FloatField(default=1.0)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.from_province.name} -> {self.to_province.name}"

    @property
    def effective_capacity_per_day(self):
        return self.capacity_per_day * self.condition


class GameTick(models.Model):
    current_tick = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Game Tick"
        verbose_name_plural = "Game Tick"

    def __str__(self):
        return f"Tick {self.current_tick}"
