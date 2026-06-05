import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class RavioliConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.ravioli'
    verbose_name = 'Knowledge Graph'

    def ready(self):
        try:
            from .signals import register_graph_signals
            register_graph_signals()
        except Exception as exc:
            logger.warning("ravioli: failed to register graph signals: %s: %s", type(exc).__name__, exc)

        try:
            from . import predefined_tasks  # noqa: F401 — registers ravioli workflow tasks
        except Exception as exc:
            logger.error("ravioli: failed to load predefined_tasks — workflow nodes will not work: %s: %s", type(exc).__name__, exc)
