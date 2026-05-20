from django.apps import AppConfig


class EventsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.events'

    def ready(self):
        from toto.core.plugin_autodiscover import autodiscover_plugins
        autodiscover_plugins("plugins.profile_plugins")
