from django.apps import AppConfig


class ManaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.mana"
    label = "mana"
    verbose_name = "Mana"

    def ready(self):
        self._connect_signup()

    @staticmethod
    def _connect_signup():
        """A new member starts with three full pools.

        What MANA's signup grant used to be, in the shape members now see. The
        fill never raises (``fill_pools``), so an empty reserve or a host with
        no pools yet cannot break registration.
        """
        from django.contrib.auth import get_user_model
        from django.db.models.signals import post_save

        def _on_user_save(sender, instance, created, **kwargs):
            if not created or kwargs.get("raw"):
                return
            from .services import fill_pools

            fill_pools(instance)

        post_save.connect(_on_user_save, sender=get_user_model(),
                          dispatch_uid="mana_fill_pools_on_user_create")
