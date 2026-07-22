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
        # Discover service plugins across all installed apps.
        from toto.core.plugin_autodiscover import autodiscover_plugins
        autodiscover_plugins("plugins.file_service_plugins")
