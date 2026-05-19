from django.apps import AppConfig


class AssetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.assets"
    verbose_name = "Assets"

    def ready(self):
        from toto.core.plugin_autodiscover import autodiscover_plugins
        autodiscover_plugins("plugins.asset_plugins")
