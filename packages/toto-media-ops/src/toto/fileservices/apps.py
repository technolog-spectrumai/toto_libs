from django.apps import AppConfig


class FileservicesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.fileservices"

    def ready(self):
        # Register the workflow node entrypoint.
        try:
            from . import predefined_tasks  # noqa: F401
        except Exception:
            pass
        # NOTE: plugin discovery is NOT here any more. It moved to
        # VaultConfig.ready() with the registry itself, because it has to happen
        # on hosts that do not install this app — the vault owns the files, and
        # a builder-backed service needs no run substrate. Calling
        # autodiscover_plugins a second time here would import every plugin
        # module twice, and BasePlugin.register raises on a duplicate key.
