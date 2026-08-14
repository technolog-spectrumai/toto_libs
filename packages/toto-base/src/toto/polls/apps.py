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
        from . import governance  # noqa: F401

        autodiscover_plugins("electorates")
        # Judges: what a scope's own rules make of a finished count. The
        # engine ships none — it counts, it does not judge.
        autodiscover_plugins("governance")
