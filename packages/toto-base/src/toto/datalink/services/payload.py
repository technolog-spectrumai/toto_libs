"""The wire envelope: schema version, keyset cursor, manifest, and a page of rows.

Three decisions here are load-bearing and easy to undo by accident.

**The cursor is opaque, and ordered by primary key.** Opaque because a primary key is
per-instance and must never appear in a payload — only the peer that issued a cursor
interprets it. Ordered by pk rather than by a modification timestamp because the peer is
*live*: ordering by ``updated_at`` lets a row edited mid-read move behind the cursor and
never be seen. A pk ordering is index-backed and total. (Several models here have UUID
primary keys; that is still a stable total order, just not insertion order.)

**A page is bounded in bytes, not only in rows.** ``toto.api.client`` caps a response
and, when it trims one, sets ``truncated`` and leaves the ``json`` key *absent* — so a
caller doing ``.get("json", {})`` silently processes nothing. Three defences follow: the
server stops serialising at a byte budget well under the transport cap, the client sets
its own cap explicitly, and truncation is a hard failure rather than a warning. This
module owns the first.

**A stage the peer cannot count cheaply reports ``row_count: null``**, and that single
fact is what switches the receiver's progress bar from determinate to indeterminate.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Iterable

from django.utils import timezone

from ..registry import (
    STAGE_TITLES,
    STAGES,
    SyncPolicy,
    registry_digest,
    stage_models,
)
from .canonical import canonical_json
from .serialize import serialize_row

# The wire format's own version. Bump on ANY change to the payload shape.
#
# Compared for EXACT equality between peers, not ">=" — the receiver writes these rows
# straight into its own tables, so a field it misreads becomes silent bad data with no
# round trip to catch it. Refusing is cheaper than being clever.
DATALINK_SCHEMA_VERSION = 1

# Rows per page unless the caller says otherwise, and the ceiling on what it may ask
# for. 200/1000 rather than the backup engine's "all of them": that engine loads every
# row of every table into a list comprehension and then json.dumps the lot.
DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 1000

# Stop serialising a page at this many bytes. Half of toto.api.client's 1 MiB default,
# so a page cannot approach the transport cap however large the rows are.
MAX_PAGE_BYTES = 512 * 1024


class BadCursor(ValueError):
    """A cursor that did not come from this peer, or was corrupted in transit."""


def encode_cursor(after_pk: Any) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"after_pk": str(after_pk)}, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def decode_cursor(cursor: str | None) -> str | None:
    """The primary key to read after, or None to start at the beginning.

    A malformed cursor raises. It must never silently mean "start again", because a
    resume that restarts on a bad cursor loops forever instead of failing.
    """
    if not cursor:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        return str(payload["after_pk"])
    except Exception as exc:  # noqa: BLE001 - any malformation is the same answer
        raise BadCursor(f"unreadable cursor: {exc}") from exc


def instance_descriptor() -> dict[str, Any]:
    """How this instance introduces itself. Never includes a credential."""
    from toto.core.models import Platform

    platform = Platform.objects.filter(active=True).first()
    return {
        "site_name": getattr(platform, "site_name", "") or "",
        "domain": getattr(platform, "domain", "") or "",
    }


def build_manifest(*, granted_stages: Iterable[str] | None = None,
                   counts: bool = True) -> dict[str, Any]:
    """What this peer holds, stage by stage.

    ``granted_stages`` is the scope on the reading peer's grant. An ungranted stage is
    **listed with ``granted: false`` and no counts**, never omitted — so the receiver's
    UI can say "the peer did not grant this" instead of pretending the stage does not
    exist, and no counting work is done for it.
    """
    granted = None if granted_stages is None else set(granted_stages)
    stages: list[dict[str, Any]] = []

    for index, stage in enumerate(STAGES):
        policies = stage_models(stage)
        is_granted = granted is None or stage in granted
        entry: dict[str, Any] = {
            "key": stage,
            "title": STAGE_TITLES.get(stage, stage),
            "order": index,
            "granted": is_granted,
            "models": [],
            "row_count": None,
        }
        if is_granted and counts:
            total = 0
            countable = True
            for policy in policies:
                model_count = _count_rows(policy)
                entry["models"].append({
                    "model": policy.model_label,
                    "row_count": model_count,
                })
                if model_count is None:
                    countable = False
                else:
                    total += model_count
            entry["row_count"] = total if countable else None
        else:
            entry["models"] = [{"model": p.model_label, "row_count": None} for p in policies]
        stages.append(entry)

    return {
        "datalink": DATALINK_SCHEMA_VERSION,
        "kind": "manifest",
        "instance": instance_descriptor(),
        "registry_digest": registry_digest(),
        "generated_at": timezone.now(),
        "stages": stages,
        # On the wire on purpose: a machine-checkable statement of the two scope
        # decisions, which the harness asserts rather than trusting prose.
        "invariants": {
            "replicates_user_accounts": False,
            "replicates_file_bytes": False,
        },
    }


def _count_rows(policy: SyncPolicy) -> int | None:
    """``.count()``, or None when the model is not there to count.

    Never ``len(list(...))`` — the point of counting is to size the progress bar
    without loading the table.
    """
    from django.apps import apps

    try:
        model = apps.get_model(policy.model_label)
    except LookupError:
        return None
    try:
        return model._default_manager.count()
    except Exception:  # noqa: BLE001 - a count must never fail a manifest
        return None


def _page_queryset(policy: SyncPolicy, after_pk: str | None):
    """Rows after the cursor, in pk order, with the joins the policy needs.

    ``select_related`` on every forward reference and ``prefetch_related`` on every m2m
    is not an optimisation here, it is the difference between one query per chunk and
    one per row: ``backup_engine`` does neither, so it issues a query per foreign key
    per row and calls ``.all()`` on every m2m it does not even serialise.
    """
    from django.apps import apps

    model = apps.get_model(policy.model_label)
    queryset = model._default_manager.all()

    relations = []
    for name in policy.fields:
        try:
            field = model._meta.get_field(name)
        except Exception:
            continue
        if field.is_relation and (field.many_to_one or field.one_to_one):
            relations.append(name)
    if relations:
        queryset = queryset.select_related(*relations)
    if policy.m2m:
        queryset = queryset.prefetch_related(*policy.m2m)

    if after_pk is not None:
        queryset = queryset.filter(pk__gt=after_pk)
    return queryset.order_by("pk")


def serialize_page(policy: SyncPolicy, *, cursor: str | None = None,
                   limit: int = DEFAULT_PAGE_SIZE,
                   max_bytes: int = MAX_PAGE_BYTES) -> dict[str, Any]:
    """One page of a model's rows, bounded by both ``limit`` and ``max_bytes``."""
    limit = max(1, min(int(limit), MAX_PAGE_SIZE))
    after_pk = decode_cursor(cursor)
    queryset = _page_queryset(policy, after_pk)

    rows: list[dict[str, Any]] = []
    budget = 0
    last_pk = None
    has_more = False

    # limit + 1 so "are there more?" is answered without a second query.
    for obj in queryset[: limit + 1].iterator(chunk_size=limit + 1):
        if len(rows) >= limit:
            has_more = True
            break
        row = serialize_row(obj, policy)
        size = len(canonical_json(row))
        if rows and budget + size > max_bytes:
            # Stop early rather than emit a page the transport would trim. Returning
            # fewer rows with a valid cursor is always safe; a truncated page is not.
            has_more = True
            break
        rows.append(row)
        budget += size
        last_pk = obj.pk

    return {
        "datalink": DATALINK_SCHEMA_VERSION,
        "kind": "page",
        "stage": policy.stage,
        "model": policy.model_label,
        "identity": {"strategy": policy.identity, "natural_key": list(policy.natural_key)},
        "rows": rows,
        "count": len(rows),
        "has_more": has_more,
        "next_cursor": encode_cursor(last_pk) if (has_more and last_pk is not None) else None,
        "generated_at": timezone.now(),
    }
