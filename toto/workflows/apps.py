from importlib import import_module

from django.apps import AppConfig


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
            except Exception:
                pass  # Never break startup due to a bad predefined_tasks import.
