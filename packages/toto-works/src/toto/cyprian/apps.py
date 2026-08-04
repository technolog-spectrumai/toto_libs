from django.apps import AppConfig


class CyprianConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.cyprian"
    verbose_name = "Cyprian"

    def ready(self):
        from . import checks  # noqa: F401  (registers the system checks)
