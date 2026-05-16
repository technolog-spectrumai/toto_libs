from django.apps import AppConfig


class SocialHubConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.socialhub'

    def ready(self):
        from toto.core.plugin_autodiscover import autodiscover_plugins

        autodiscover_plugins("plugins.profile_plugins")
        autodiscover_plugins("plugins.community_plugins")
