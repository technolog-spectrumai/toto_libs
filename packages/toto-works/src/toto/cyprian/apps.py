from django.apps import AppConfig


class CyprianConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.cyprian"
    verbose_name = "Cyprian"

    def ready(self):
        from . import checks  # noqa: F401  (registers the system checks)
        from toto.core.plugin_autodiscover import autodiscover_plugins

        # Owning apps declare their claim over documents here — see bridge.py.
        # Discovery runs from CYPRIAN's ready() rather than each owner's, so an
        # app can ship a bridge to a host that has no writer and pay nothing for
        # it: the module is simply never imported there.
        autodiscover_plugins("plugins.cyprian_bridges")
