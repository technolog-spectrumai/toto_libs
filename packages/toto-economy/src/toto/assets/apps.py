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
        from toto.assets.prepaid import get_or_create_prepaid_account, grant_starting_gas

        def _on_user_save(sender, instance, created, **kwargs):
            if not created:
                return
            get_or_create_prepaid_account(instance)
            # Fund it in the same breath. A signup that leaves the account at
            # zero produces a user who is refused everything and cannot tell
            # why. grant_starting_gas swallows its own failures precisely so a
            # missing ledger or an empty reserve cannot break registration.
            grant_starting_gas(instance)

        post_save.connect(_on_user_save, sender=get_user_model(),
                          dispatch_uid="assets_create_prepaid_on_user_create")
