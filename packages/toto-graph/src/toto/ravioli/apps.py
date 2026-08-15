import logging

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class RavioliConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.ravioli'
    verbose_name = _('Knowledge Graph')

    def ready(self):
        # SQL→Neo4j projection signals now live in the toto.sql_neo4j_sync app.
        try:
            from . import predefined_tasks  # noqa: F401 — registers ravioli query/analysis tasks
        except Exception as exc:
            logger.error("ravioli: failed to load predefined_tasks — workflow nodes will not work: %s: %s", type(exc).__name__, exc)
