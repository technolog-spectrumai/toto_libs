from django.apps import AppConfig


class NomadConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.nomad"

    def ready(self):
        # Register the "Onion Identity" profile-settings plugin. This only imports
        # the plugin module; the onion is *published* by the nomad_ensure_onion
        # management command (entrypoint), never here — ready() runs in every
        # worker and during migrations.
        from toto.core.plugin_autodiscover import autodiscover_plugins
        autodiscover_plugins("plugins.profile_plugins")
