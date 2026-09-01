from django.apps import AppConfig


class AmbrosiaConfig(AppConfig):
    """The shared workspace base. The language apps (toto.dracena,
    toto.texlab) register into `registry.py` from their own ready()."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.ambrosia"
    label = "ambrosia"
    verbose_name = "Ambrosia (workspace base)"
