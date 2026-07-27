import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class SqlNeo4jSyncConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.sql_neo4j_sync"
    verbose_name = "SQL → Neo4j Sync"

    def ready(self):
        from django.conf import settings

        # Graph is opt-in: nothing reaches Neo4j on save/delete unless
        # RAVIOLI_AUTO_SYNC is explicitly enabled. The per-object "Export to
        # graph" button does not depend on these signals — it syncs
        # synchronously via toto.ravioli.graph_export.GraphExporter.
        if getattr(settings, "RAVIOLI_AUTO_SYNC", False):
            try:
                from .signals import register_graph_signals
                register_graph_signals()
            except Exception as exc:
                logger.warning(
                    "sql_neo4j_sync: failed to register graph signals: %s: %s",
                    type(exc).__name__, exc,
                )

        try:
            from . import predefined_tasks  # noqa: F401 — registers sync workflow tasks
        except Exception as exc:
            logger.error(
                "sql_neo4j_sync: failed to load predefined_tasks — workflow nodes will not work: %s: %s",
                type(exc).__name__, exc,
            )
