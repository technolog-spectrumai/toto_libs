from django.apps import AppConfig


class NotifyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.notify"
    label = "notify"
    verbose_name = "Notifications"

    def ready(self):
        # The bell registers by explicit import, as the mana chip does
        # (toto.mana.apps) — one widget, no discovery.
        from .plugins import header_plugins  # noqa: F401

        # What makes a notification: the vault's file events, shares,
        # clearances, jobs, the data export and the erasure request
        # (sources.py). Each only where its app is installed.
        from . import sources

        sources.connect()
