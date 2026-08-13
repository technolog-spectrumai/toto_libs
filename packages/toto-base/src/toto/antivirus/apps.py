from django.apps import AppConfig


class AntivirusConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.antivirus"
    verbose_name = "Antivirus"

    def ready(self):
        # Scanners other apps declare in <app>/scanners.py. Discovery runs from
        # HERE rather than each owner's ready(), so an app can ship a scanner to
        # a host with no antivirus and pay nothing for it — the module is simply
        # never imported there. Same reasoning as cyprian's bridges.
        from toto.core.plugin_autodiscover import autodiscover_plugins

        from . import scanners  # noqa: F401  - registers the built-ins

        autodiscover_plugins("scanners")
