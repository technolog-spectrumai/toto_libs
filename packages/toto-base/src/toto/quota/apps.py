from django.apps import AppConfig


class QuotaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.quota"

    def ready(self):
        # Collect every app's metric declarations. Safe from here wherever quota
        # sits in INSTALLED_APPS: Django imports every models.py before it calls
        # any ready(), so apps installed later are already importable.
        from toto.core.plugin_autodiscover import autodiscover_plugins

        autodiscover_plugins("metrics")
