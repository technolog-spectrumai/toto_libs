from django.apps import AppConfig


class GitvaultConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.gitvault"

    def ready(self):
        from . import signals  # noqa: F401
        # Workflow-engine task (workflows' ready() also autodiscovers this;
        # the explicit import matches fileservices/texlab and is harmless).
        try:
            from . import predefined_tasks  # noqa: F401
        except Exception:
            pass
