from django.apps import AppConfig


class SocialHubConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.socialhub'

    def ready(self):
        from toto.core.plugin_autodiscover import autodiscover_plugins

        autodiscover_plugins("plugins.profile_plugins")
        autodiscover_plugins("plugins.community_plugins")

        # Communities, clearances, members, applications and references on the
        # audit chain (2026-09-28): audit.py.
        from . import audit

        audit.connect()
