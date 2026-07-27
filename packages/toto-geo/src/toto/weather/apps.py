from django.apps import AppConfig


class WeatherConfig(AppConfig):
    name = "toto.weather"
    verbose_name = "Weather"

    def ready(self):
        from django.conf import settings
        from django.core.exceptions import ImproperlyConfigured

        # Weather reads Address geometry and renders map overlays; it cannot run
        # on a GIS-off host. The build normally rejects this in
        # features.resolve_features (BUILD_WEATHER + BUILD_GEO=0); this is the
        # defensive backstop for a hand-assembled INSTALLED_APPS.
        if not getattr(settings, "HAS_GIS", True):
            raise ImproperlyConfigured(
                "toto.weather requires BUILD_GEO=1 (it reads Address geometry); "
                "this host was built without GIS."
            )

        from django.apps import apps

        if apps.is_installed("toto.tactical"):
            from toto.tactical.plugins import FieldMapPlugin, FieldMetricsPlugin
            from toto.weather.plugins.field_plugins import (
                weather_map_features,
                weather_metrics_section,
            )
            FieldMapPlugin.register(weather_map_features)
            FieldMetricsPlugin.register(weather_metrics_section)

        from toto.weather import predefined_tasks  # noqa: F401 — registers weather workflow tasks
