from django.apps import AppConfig


class SocialHubConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.socialhub'

    def ready(self):
        from toto.core.plugin_autodiscover import autodiscover_plugins

        autodiscover_plugins("plugins.profile_plugins")
        autodiscover_plugins("plugins.community_plugins")
        self._connect_treasury_signal()

    @staticmethod
    def _connect_treasury_signal():
        from django.db.models.signals import post_save
        from toto.socialhub.models import Community
        from toto.socialhub.treasury import get_or_create_treasury_account

        def _on_community_save(sender, instance, created, **kwargs):
            if created:
                get_or_create_treasury_account(instance)

        post_save.connect(_on_community_save, sender=Community,
                          dispatch_uid="socialhub_create_treasury_on_community_create")
