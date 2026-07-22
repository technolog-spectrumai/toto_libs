from django.contrib import admin
from .models import WeatherSettings, WeatherObservation, ForecastSession, ForecastPoint


@admin.register(WeatherSettings)
class WeatherSettingsAdmin(admin.ModelAdmin):
    list_display = ("current_provider", "forecast_provider")
    fields = ("current_provider", "forecast_provider")

    def has_add_permission(self, request):
        return not WeatherSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class ForecastPointInline(admin.TabularInline):
    model = ForecastPoint
    extra = 0
    fields = ("address", "valid_at", "temperature", "precipitation_mm", "cloud_cover", "wind_speed_kmh")
    readonly_fields = fields
    show_change_link = False
    max_num = 0
    can_delete = False


@admin.register(WeatherObservation)
class WeatherObservationAdmin(admin.ModelAdmin):
    list_display = (
        "address", "provider", "temperature", "cloud_cover",
        "wind_speed_kmh", "observed_at", "loaded_at",
    )
    list_filter = ("provider",)
    search_fields = ("address__locality_name", "address__street")
    readonly_fields = (
        "address", "provider", "workflow_run", "observed_at", "loaded_at",
        "temperature", "precipitation_mm", "precipitation_type",
        "cloud_cover", "wind_speed_kmh", "wind_direction_deg", "visibility_km",
    )
    ordering = ("-loaded_at",)


@admin.register(ForecastSession)
class ForecastSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "provider", "valid_from", "valid_to", "resolution_hours", "loaded_at")
    list_filter = ("provider",)
    readonly_fields = ("provider", "workflow_run", "loaded_at", "valid_from", "valid_to", "resolution_hours")
    inlines = [ForecastPointInline]
    ordering = ("-loaded_at",)
