from django.apps import AppConfig


class RavioliConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.ravioli'
    verbose_name = 'Knowledge Graph'

    def ready(self):
        from .signals import register_graph_signals

        register_graph_signals()
        from . import predefined_tasks  # noqa: F401 — registers ravioli workflow tasks
