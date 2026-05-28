from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.inventory"
    verbose_name = "Inventory"

    def ready(self):
        from toto.core.plugin_autodiscover import autodiscover_plugins
        autodiscover_plugins("plugins.inventory_object_plugins")

        from toto.tactical.plugins import FieldMapPlugin, FieldMetricsPlugin
        from toto.inventory.plugins.field_plugins import (
            inventory_map_features,
            inventory_metrics_section,
        )
        FieldMapPlugin.register(inventory_map_features)
        FieldMetricsPlugin.register(inventory_metrics_section)
