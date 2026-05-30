from django.apps import AppConfig


class WeatherConfig(AppConfig):
    name = "toto.weather"
    verbose_name = "Weather"

    def ready(self):
        from django.apps import apps

        if apps.is_installed("toto.tactical"):
            from toto.tactical.plugins import FieldMapPlugin, FieldMetricsPlugin
            from toto.weather.plugins.field_plugins import (
                weather_map_features,
                weather_metrics_section,
            )
            FieldMapPlugin.register(weather_map_features)
            FieldMetricsPlugin.register(weather_metrics_section)

        from toto.locations.plugins.map_layer_plugins import LocationMapLayerPlugin
        from toto.weather.plugins.location_layer_plugins import (
            weather_temperature_layer,
            weather_precipitation_layer,
        )
        LocationMapLayerPlugin.register(weather_temperature_layer)
        LocationMapLayerPlugin.register(weather_precipitation_layer)
