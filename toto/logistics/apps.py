from django.apps import AppConfig


class LogisticsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.logistics"
    verbose_name = "Logistics"

    def ready(self):
        from toto.field.plugins import FieldMapPlugin, FieldMetricsPlugin
        from toto.logistics.plugins.field_plugins import (
            logistics_map_features,
            logistics_metrics_section,
        )
        FieldMapPlugin.register(logistics_map_features)
        FieldMetricsPlugin.register(logistics_metrics_section)
