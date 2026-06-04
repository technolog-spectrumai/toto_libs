from django.apps import AppConfig


class MagistrateConfig(AppConfig):
    name = "toto.magistrate"
    label = "magistrate"
    verbose_name = "Magistrate"

    def ready(self):
        from toto.core.plugin_autodiscover import autodiscover_plugins
        autodiscover_plugins("plugins.profile_plugins")
