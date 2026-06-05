import logging
from importlib import import_module

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class WorkflowsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.workflows"
    label = "workflows"

    def ready(self):
        """Auto-discover predefined_tasks modules in every installed app.

        This mirrors Django's admin.autodiscover() pattern so that tasks are
        registered regardless of which BUILD_* flags are active in the current
        process (web server, Celery worker, management command).
        """
        from django.apps import apps

        for app_config in apps.get_app_configs():
            module_path = f"{app_config.name}.predefined_tasks"
            try:
                import_module(module_path)
            except ImportError:
                pass  # App has no predefined_tasks — that's fine.
            except Exception as exc:
                logger.warning(
                    "Failed to load predefined_tasks for %s: %s: %s",
                    app_config.name,
                    type(exc).__name__,
                    exc,
                )
