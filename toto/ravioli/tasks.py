import logging
import uuid

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

_SEARCH_CACHE_TTL = 300  # 5 minutes


@shared_task(name="toto.ravioli.tasks.run_graph_search", ignore_result=True)
def run_graph_search(run_id, q, mode, limit, exact):
    """Execute a graph search in a worker and store results in Django cache."""
    from django.core.cache import cache
    from .services.search import (
        SearchUnavailableError,
        advanced_search,
        basic_search,
        deep_search,
    )

    cache.set(f"search:{run_id}:status", "running", timeout=_SEARCH_CACHE_TTL)
    try:
        if mode == "deep":
            results = deep_search(q, limit=limit, exact=exact)
        elif mode == "advanced":
            results = advanced_search(q, limit=limit)
        else:
            results = basic_search(q, limit=limit)
        cache.set(f"search:{run_id}:results", results, timeout=_SEARCH_CACHE_TTL)
        cache.set(f"search:{run_id}:status", "done", timeout=_SEARCH_CACHE_TTL)
    except SearchUnavailableError as exc:
        cache.set(f"search:{run_id}:error", str(exc), timeout=_SEARCH_CACHE_TTL)
        cache.set(f"search:{run_id}:status", "error", timeout=_SEARCH_CACHE_TTL)
    except Exception as exc:
        logger.exception("run_graph_search failed: %s", exc)
        cache.set(f"search:{run_id}:error", str(exc), timeout=_SEARCH_CACHE_TTL)
        cache.set(f"search:{run_id}:status", "error", timeout=_SEARCH_CACHE_TTL)
        raise


@shared_task(name="toto.ravioli.tasks.periodic_graph_sync", ignore_result=True)
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

    from .connection import is_enabled
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
        from .connection import Neo4jClient
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
        logger.info("ravioli periodic sync completed successfully")

    except Exception as exc:
        config.last_run_status = GraphSyncSchedule.STATUS_FAILED
        config.last_error = str(exc)
        config.save(update_fields=["last_run_status", "last_error"])
        logger.exception("ravioli periodic sync failed: %s", exc)
        raise
