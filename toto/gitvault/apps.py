from django.apps import AppConfig


class GitvaultConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.gitvault"

    def ready(self):
        from . import signals  # noqa: F401
