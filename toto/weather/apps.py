from django.apps import AppConfig


class WeatherConfig(AppConfig):
    name = "toto.weather"
    verbose_name = "Weather"

    def ready(self):
        from toto.tactical.plugins import FieldMapPlugin, FieldMetricsPlugin
        from toto.weather.plugins.field_plugins import (
            weather_map_features,
            weather_metrics_section,
        )
        FieldMapPlugin.register(weather_map_features)
        FieldMetricsPlugin.register(weather_metrics_section)
