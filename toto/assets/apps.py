from django.apps import AppConfig


class AssetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.assets"
    verbose_name = "Assets"

    def ready(self):
        from toto.core.plugin_autodiscover import autodiscover_plugins
        autodiscover_plugins("plugins.asset_plugins")
        autodiscover_plugins("plugins.profile_plugins")
        self._connect_prepaid_signal()

    @staticmethod
    def _connect_prepaid_signal():
        from django.contrib.auth import get_user_model
        from django.db.models.signals import post_save
        from toto.assets.prepaid import get_or_create_prepaid_account

        def _on_user_save(sender, instance, created, **kwargs):
            if created:
                get_or_create_prepaid_account(instance)

        post_save.connect(_on_user_save, sender=get_user_model(),
                          dispatch_uid="assets_create_prepaid_on_user_create")
