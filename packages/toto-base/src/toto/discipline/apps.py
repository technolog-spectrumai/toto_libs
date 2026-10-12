from django.apps import AppConfig


class DisciplineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.discipline"
    label = "discipline"
    verbose_name = "Discipline"

    def ready(self):
        from . import checks  # noqa: F401
        from . import sources  # noqa: F401
        # The chip registers by explicit import, as every header widget does.
        from .plugins import header_plugins  # noqa: F401

        self._connect_signup()

    @staticmethod
    def _connect_signup():
        """A new account gets its first balance. Never raises: a file that
        cannot be read must not stop somebody being made."""
        from django.contrib.auth import get_user_model
        from django.db.models.signals import post_save

        def _on_user_save(sender, instance, created, **kwargs):
            if not created or kwargs.get("raw"):
                return
            try:
                from .services import signup

                signup(instance)
            except Exception:  # noqa: BLE001
                import logging

                logging.getLogger("toto.discipline").exception("discipline: signup of %s",
                                                               instance.pk)

        # weak=False: a local function is collected the moment this returns.
        post_save.connect(_on_user_save, sender=get_user_model(), weak=False,
                          dispatch_uid="discipline_signup_on_user_create")
