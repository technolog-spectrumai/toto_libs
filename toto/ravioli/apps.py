from django.apps import AppConfig


class RavioliConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.ravioli'

    def ready(self):
        from .signals import register_graph_signals

        register_graph_signals()
