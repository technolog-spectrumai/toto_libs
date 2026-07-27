"""Message search, dual-backend.

Every host runs **SpatiaLite in dev and in every clean-env gate, PostGIS in deployment**
(see each host's ``settings.py``). So search branches on ``connection.vendor`` at query
time rather than committing to one engine in the schema:

* **postgresql** — ``SearchVector``/``SearchQuery``/``SearchRank``, computed per query.
* **anything else** — case-insensitive substring matching.

Deliberately *no* ``SearchVectorField`` and *no* ``GinIndex`` in the migration: Postgres-only
DDL in ``Meta.indexes`` breaks ``manage.py migrate`` against the throwaway SpatiaLite
database used by dev and by both hosts' ``scripts/clean_env_test.sh``. Promoting to a stored,
indexed vector behind a vendor-guarded ``RunPython`` remains deferred work.

The precedent for branching on the backend this way is ``toto/core/apps.py``; the idiom for
telling the UI which engine actually ran is ravioli's ``effective_mode``/``fallback_used``
context dict.
"""
from django.db import connection

from . import permissions


def _is_postgres():
    return connection.vendor == "postgresql"


def search_mode():
    """Context describing which engine served the query, for the template to surface."""
    if _is_postgres():
        return {
            "search_mode": "fulltext",
            "search_engine": "postgresql",
            "search_fallback_used": False,
        }
    return {
        "search_mode": "substring",
        "search_engine": connection.vendor,
        "search_fallback_used": True,
    }


def search_messages(user, query, *, channel_slug=None):
    """Messages matching ``query``, ranked, scoped to what ``user`` may read.

    Scoping is not optional: messages are permanent now, so a leak here is durable.
    Only channels with an active membership row for this user are ever searched.
    """
    from .models import ForumMessage

    query = (query or "").strip()
    if not query:
        return ForumMessage.objects.none()

    channels = permissions.readable_channels(user)
    if channel_slug:
        channels = channels.filter(slug=channel_slug)

    qs = (
        ForumMessage.objects.filter(channel__in=channels, deleted_at__isnull=True)
        .exclude(body="")
        .select_related("channel")
    )

    if _is_postgres():
        from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector

        vector = SearchVector("body", config="english")
        search_query = SearchQuery(query, config="english", search_type="websearch")
        return (
            qs.annotate(rank=SearchRank(vector, search_query))
            .filter(rank__gt=0)
            .order_by("-rank", "-created_at")
        )

    return qs.filter(body__icontains=query).order_by("-created_at")
