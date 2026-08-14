from django.apps import AppConfig


class PollsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.polls'

    def ready(self):
        # Electorates other apps declare in <app>/electorates.py — the same
        # contract as antivirus scanners: discovery runs from HERE, pure data,
        # no DB, ready()-safe.
        from toto.core.plugin_autodiscover import autodiscover_plugins

        from . import electorates  # noqa: F401  - registers the built-ins

        autodiscover_plugins("electorates")
