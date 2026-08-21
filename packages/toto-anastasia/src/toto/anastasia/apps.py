from django.apps import AppConfig


class AnastasiaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.anastasia"
    verbose_name = "Compute Gears"

    def ready(self):
        # Registers the half-configured-deployment warnings. Imported here
        # rather than at module scope so `checks` can read settings that are
        # only final once the app registry is populated.
        from . import checks  # noqa: F401
