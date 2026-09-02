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

        from . import catalogue, checks, metrics  # noqa: F401

        autodiscover_plugins("entitlements")

        # The ladder, AFTER autodiscovery: a plan may name a feature an app
        # ships itself, so the catalogue has to be complete before the file is
        # validated against it. A structural fault raises here and the host
        # does not boot — an unreadable plan file is a build defect, and
        # `manage.py check` catches it before a deploy gets this far.
        from . import plans

        plans.reload()
        plans.load()
