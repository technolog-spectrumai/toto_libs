from django.contrib import admin

from .models import (
    Building,
    BuildingType,
    ConstructionProject,
    DepositType,
    Empire,
    GameTick,
    ItemType,
    Planet,
    Province,
    ProvinceDeposit,
    ProvinceInventory,
    ProvincePopulation,
    Recipe,
    RecipeInput,
    TransportLink,
)


class ProvinceInline(admin.TabularInline):
    model = Province
    extra = 0
    fields = ("name", "biome_display", "habitability_display", "cell_count", "width", "height", "x", "y")
    readonly_fields = ("biome_display", "habitability_display")


@admin.register(Empire)
class EmpireAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "credits", "created_at")
    search_fields = ("name", "user__username", "user__email")


@admin.register(Planet)
class PlanetAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "primary_type", "secondary_trait", "size", "province_count", "habitability_display")
    search_fields = ("name", "owner__name")
    readonly_fields = ("primary_type", "secondary_trait", "size", "habitability_display")
    inlines = [ProvinceInline]


class ProvinceDepositInline(admin.TabularInline):
    model = ProvinceDeposit
    extra = 0


class ProvinceInventoryInline(admin.TabularInline):
    model = ProvinceInventory
    extra = 0


class ProvincePopulationInline(admin.StackedInline):
    model = ProvincePopulation
    extra = 0
    max_num = 1


class BuildingInline(admin.TabularInline):
    model = Building
    extra = 0
    fields = ("building_type", "level", "status", "priority", "efficiency", "condition", "selected_recipe")


@admin.register(Province)
class ProvinceAdmin(admin.ModelAdmin):
    list_display = ("name", "planet", "biome_display", "cell_count", "used_cells", "free_cells", "infrastructure_level", "habitability_display")
    list_filter = ("planet",)
    search_fields = ("name", "planet__name")
    readonly_fields = ("biome_display", "biome_code", "biome_color", "infrastructure_level", "habitability_display", "area")
    inlines = [ProvinceDepositInline, ProvinceInventoryInline, ProvincePopulationInline, BuildingInline]


def biome_display(obj):
    return obj.biome


def habitability_display(obj):
    return round(obj.habitability, 3)


ProvinceInline.biome_display = staticmethod(biome_display)
ProvinceInline.habitability_display = staticmethod(habitability_display)
ProvinceAdmin.biome_display = staticmethod(biome_display)
ProvinceAdmin.habitability_display = staticmethod(habitability_display)
PlanetAdmin.habitability_display = staticmethod(habitability_display)


@admin.register(ItemType)
class ItemTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "tier", "cargo_class", "is_energy", "is_strategic", "base_market_value", "is_active")
    list_filter = ("tier", "cargo_class", "is_energy", "is_strategic", "is_active")
    search_fields = ("name", "code")
    prepopulated_fields = {"code": ("name",)}


@admin.register(DepositType)
class DepositTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "produces_item", "is_renewable", "is_active")
    list_filter = ("is_renewable", "is_active", "produces_item")
    search_fields = ("name", "code")
    prepopulated_fields = {"code": ("name",)}


@admin.register(ProvinceDeposit)
class ProvinceDepositAdmin(admin.ModelAdmin):
    list_display = ("province", "deposit_type", "abundance", "richness", "accessibility", "depletion", "extraction_factor")
    list_filter = ("deposit_type", "province__planet")
    search_fields = ("province__name", "deposit_type__name")


@admin.register(ProvinceInventory)
class ProvinceInventoryAdmin(admin.ModelAdmin):
    list_display = ("province", "item_type", "quantity", "capacity", "free_capacity")
    list_filter = ("item_type", "province__planet")
    search_fields = ("province__name", "item_type__name")


@admin.register(ProvincePopulation)
class ProvincePopulationAdmin(admin.ModelAdmin):
    list_display = ("province", "total_population", "laborers", "technicians", "engineers", "scientists", "administrators")
    search_fields = ("province__name",)


class RecipeInputInline(admin.TabularInline):
    model = RecipeInput
    extra = 0


@admin.register(BuildingType)
class BuildingTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "category", "cell_cost", "base_energy_demand", "max_level", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("name", "code")
    prepopulated_fields = {"code": ("name",)}


@admin.register(Building)
class BuildingAdmin(admin.ModelAdmin):
    list_display = ("building_type", "province", "level", "status", "priority", "efficiency", "condition", "selected_recipe")
    list_filter = ("status", "priority", "building_type", "province__planet")
    search_fields = ("building_type__name", "province__name", "province__planet__name")


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "building_type", "output_item", "output_quantity_per_day", "is_default", "is_active")
    list_filter = ("building_type", "output_item", "is_default", "is_active")
    search_fields = ("name", "code")
    prepopulated_fields = {"code": ("name",)}
    inlines = [RecipeInputInline]


@admin.register(RecipeInput)
class RecipeInputAdmin(admin.ModelAdmin):
    list_display = ("recipe", "item_type", "quantity_per_day")
    list_filter = ("item_type", "recipe__building_type")


@admin.register(ConstructionProject)
class ConstructionProjectAdmin(admin.ModelAdmin):
    list_display = ("building_type", "province", "target_level", "priority", "status", "progress", "required_work", "progress_percent")
    list_filter = ("status", "priority", "building_type", "province__planet")


@admin.register(TransportLink)
class TransportLinkAdmin(admin.ModelAdmin):
    list_display = ("from_province", "to_province", "mode", "capacity_per_day", "effective_capacity_per_day", "condition", "is_active")
    list_filter = ("mode", "is_active", "from_province__planet")


@admin.register(GameTick)
class GameTickAdmin(admin.ModelAdmin):
    list_display = ("current_tick", "updated_at")
