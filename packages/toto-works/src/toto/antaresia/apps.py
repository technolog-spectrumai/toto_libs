from django.apps import AppConfig


class AntaresiaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.antaresia'

    def ready(self):
        from . import predefined_tasks  # noqa: F401 — registers workflow tasks
