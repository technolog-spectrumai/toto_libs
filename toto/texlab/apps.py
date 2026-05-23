from django.apps import AppConfig


class TexlabConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.texlab'

    def ready(self):
        from . import predefined_tasks  # noqa: F401 — registers tasks
