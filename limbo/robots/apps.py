from django.apps import AppConfig


class RobotsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.robots"
    label = "robots"
    verbose_name = "Robot Fleet"

    def ready(self) -> None:
        try:
            from . import metrics  # noqa: F401 — registers Prometheus gauges on startup
        except Exception:
            pass
