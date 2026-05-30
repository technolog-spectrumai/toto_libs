from django.apps import AppConfig


class IncidentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.incidents"
    verbose_name = "Incidents"

    def ready(self):
        from django.apps import apps

        if apps.is_installed("toto.tactical"):
            from toto.tactical.plugins import FieldMapPlugin, FieldMetricsPlugin
            from toto.incidents.plugins.field_plugins import (
                incidents_map_features,
                incidents_metrics_section,
            )
            FieldMapPlugin.register(incidents_map_features)
            FieldMetricsPlugin.register(incidents_metrics_section)
