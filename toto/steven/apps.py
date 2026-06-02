from django.apps import AppConfig


class StevenConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.steven'
    verbose_name = 'Steven'

    def ready(self):
        from toto.core.plugin_autodiscover import autodiscover_plugins
        autodiscover_plugins("plugins.floating_plugins")
        from . import predefined_tasks  # noqa: F401 — registers steven workflow tasks
