from django.apps import AppConfig


class LocationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.locations'

    def ready(self):
        from toto.core.plugin_autodiscover import autodiscover_plugins
        autodiscover_plugins("plugins.sidebar_plugins")
        autodiscover_plugins("plugins.url_plugins")
        autodiscover_plugins("plugins.map_plugins")
        autodiscover_plugins("plugins.context_plugins")
        # The kinds of item a map domain holds: locations' own five, and any
        # an installed app adds (the host's places).
        autodiscover_plugins("plugins.domain_plugins")

