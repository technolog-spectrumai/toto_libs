import logging

from celery import shared_task

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
