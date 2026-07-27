import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="toto.sql_neo4j_sync.tasks.periodic_graph_sync", ignore_result=True)
def periodic_graph_sync():
    """
    Called every minute by Celery beat. Runs a full graph rebuild only if:
      - GraphSyncSchedule.enabled is True
      - RAVIOLI_ENABLED is True
      - At least interval_minutes have elapsed since last_run_at
    """
    from .models import GraphSyncSchedule

    config = GraphSyncSchedule.get_config()

    if not config.enabled:
        return

    from toto.ravioli.connection import is_enabled
    if not is_enabled():
        return

    now = timezone.now()
    if config.last_run_at is not None:
        elapsed = (now - config.last_run_at).total_seconds() / 60
        if elapsed < config.interval_minutes:
            return

    config.last_run_status = GraphSyncSchedule.STATUS_RUNNING
    config.last_run_at = now
    config.last_error = ""
    config.save(update_fields=["last_run_status", "last_run_at", "last_error"])

    try:
        from toto.ravioli.connection import Neo4jClient

        from .loader import load_all_configs
        from .projection import ProjectionRunner

        configs = load_all_configs()
        client = Neo4jClient()
        try:
            ProjectionRunner(client, configs).run()
        finally:
            client.close()

        config.last_run_status = GraphSyncSchedule.STATUS_SUCCESS
        config.last_error = ""
        config.save(update_fields=["last_run_status", "last_error"])
        logger.info("sql_neo4j_sync periodic sync completed successfully")

    except Exception as exc:
        config.last_run_status = GraphSyncSchedule.STATUS_FAILED
        config.last_error = str(exc)
        config.save(update_fields=["last_run_status", "last_error"])
        logger.exception("sql_neo4j_sync periodic sync failed: %s", exc)
        raise
