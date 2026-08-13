from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.subscriptions"
    verbose_name = "Subscriptions"

    def ready(self):
        # Pure data, no models, no database: ready() runs before migrate and
        # during collectstatic. The catalogue import registers the defaults;
        # autodiscovery lets any app add its own entitlements afterwards.
        from toto.core.plugin_autodiscover import autodiscover_plugins

        from . import catalogue, metrics  # noqa: F401

        autodiscover_plugins("entitlements")
