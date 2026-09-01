"""Append to the chain, and verify it.

Adapted from the parked Django Irena's ``toto.audit.services``. The redaction
list is kept verbatim — it is the part that was learned by finding secrets in a
log, and shortening it is how they come back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import PurePath
from uuid import UUID

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from toto.audit.canonical import payload_hash
from toto.audit.context import current_context, is_suppressed
from toto.audit.models import AuditChain, AuditRecord, chain_key

SENSITIVE_PARTS = (
    "password", "passwd", "secret", "token", "credential", "private_key",
    "ciphertext", "authorization", "cookie", "sessionid", "csrf",
)
SENSITIVE_TEXT = ("password=", "secret=", "token=", "authorization:", "private_key")


def _sensitive_key(key):
    lowered = str(key).lower()
    return any(part in lowered for part in SENSITIVE_PARTS)


def sanitize(value, *, key=""):
    """Redact by key name, clamp by length, and make everything JSON-safe.

    The length clamps are not cosmetic: an audit row that can carry an
    unbounded blob is a way to fill the disk through a form field.
    """
    if _sensitive_key(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): sanitize(v, key=k) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        if any(marker in value.lower() for marker in SENSITIVE_TEXT):
            return "[REDACTED]"
        return value[:4000]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID, PurePath)):
        return str(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:1000]


#: A UUID anywhere in a path. Several routes carry one as a BEARER credential —
#: sso_core's password-recovery link is redeemed at recover/<uuid>/ — and the
#: audit row must never become the durable copy of a secret: request_source is
#: a persisted JSONField AND part of the hash-chain material, so it cannot be
#: scrubbed after the fact without breaking verify_chain. UUIDs that are mere
#: object ids lose nothing by the redaction; the record's own object_id names
#: the object properly.
_UUID_SEGMENT = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def request_source(request):
    if request is None:
        return {}
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = forwarded.split(",", 1)[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "")
    return sanitize({
        "method": request.method,
        "path": _UUID_SEGMENT.sub("[uuid]", request.path)[:1000],
        "ip_address": ip,
        "user_agent": request.META.get("HTTP_USER_AGENT", "")[:500],
    })


def _object_identity(obj, object_type="", object_id="", description=""):
    if obj is None:
        return None, object_type, str(object_id or ""), description
    content_type = ContentType.objects.get_for_model(obj, for_concrete_model=False)
    return (
        content_type,
        object_type or f"{content_type.app_label}.{content_type.model}",
        str(object_id or obj.pk),
        description or str(obj)[:500],
    )


def record_material(record):
    """Exactly the fields the digest covers.

    Written out field by field rather than serialising the model, so adding a
    column cannot silently change every future hash while leaving the existing
    rows verifying against the old shape.
    """
    return {
        "algorithm": record.algorithm,
        "chain": record.chain.key,
        "sequence": record.sequence,
        "previous_hash": record.previous_hash,
        "timestamp": record.timestamp.isoformat(),
        "actor_user_id": record.actor_user_id,
        "actor_username": record.actor_username,
        "action": record.action,
        "app_label": record.app_label,
        "object_type": record.object_type,
        "object_id": record.object_id,
        "object_description": record.object_description,
        "changes": record.changes,
        "request_source": record.request_source,
        "correlation_id": record.correlation_id,
        "source": record.source,
        "success": record.success,
        "metadata": record.metadata,
        "dedupe_key": record.dedupe_key or "",
    }


#: Pass as ``actor_user`` for an event with NO actor — one the system did on
#: its own timetable, whoever's request happened to host it. Plain ``None``
#: means "unspecified" and falls back to the ambient request's user.
SYSTEM = object()


@transaction.atomic
def record(
    action,
    *,
    app_label="",
    obj=None,
    object_type="",
    object_id="",
    description="",
    actor_user=None,
    before=None,
    after=None,
    changes=None,
    request=None,
    source="",
    correlation_id="",
    success=True,
    metadata=None,
    timestamp=None,
    dedupe_key=None,
):
    """Append one record and return it (or the existing one, if deduped)."""
    if is_suppressed():
        return None
    if dedupe_key:
        existing = AuditRecord.objects.filter(dedupe_key=dedupe_key).first()
        if existing:
            return existing

    context = current_context()
    request = request if request is not None else (context.request if context else None)
    if actor_user is SYSTEM:
        # A system event that merely HAPPENS during somebody's request — an
        # expiry sweep on a page render, say. The ambient context would name
        # the browsing user as the actor of something they did not do.
        actor_user = None
    elif actor_user is None and context is not None:
        actor_user = context.user
    # AnonymousUser has no pk and cannot be a FK target.
    if actor_user is not None and not getattr(actor_user, "pk", None):
        actor_user = None
    source = source or (context.source if context else "system")
    correlation_id = correlation_id or (context.correlation_id if context else "")

    content_type, object_type, object_id, description = _object_identity(
        obj, object_type, object_id, description
    )
    if not app_label:
        app_label = content_type.app_label if content_type else "system"

    if changes is None:
        before = sanitize(before or {})
        after = sanitize(after or {})
        changes = {
            key: {"before": before.get(key), "after": after.get(key)}
            for key in sorted(set(before) | set(after))
            if before.get(key) != after.get(key)
        }
    else:
        changes = sanitize(changes)

    chain, _ = AuditChain.objects.get_or_create(key=chain_key())
    # SELECT ... FOR UPDATE is what makes the sequence contiguous under
    # concurrency: two requests appending at once would otherwise both read
    # the same tail and both claim sequence n+1.
    chain = AuditChain.objects.select_for_update().get(pk=chain.pk)
    previous = chain.records.order_by("-sequence").first()

    entry = AuditRecord(
        chain=chain,
        sequence=(previous.sequence + 1) if previous else 1,
        previous_hash=previous.record_hash if previous else "",
        timestamp=timestamp or timezone.now(),
        actor_user=actor_user,
        actor_username=(actor_user.get_username() if actor_user else "")[:150],
        action=str(action).upper()[:100],
        app_label=app_label[:100],
        object_type=object_type[:150],
        object_id=object_id[:255],
        object_description=(description or "")[:500],
        content_type=content_type,
        changes=changes,
        request_source=request_source(request),
        correlation_id=correlation_id[:128],
        source=source[:80],
        success=bool(success),
        metadata=sanitize(metadata or {}),
        dedupe_key=dedupe_key,
    )
    entry.record_hash = payload_hash(record_material(entry))
    entry.save(force_insert=True)
    return entry


def change(action, *, obj, before, after, **kwargs):
    return record(action, obj=obj, before=before, after=after, **kwargs)


def event(action, **kwargs):
    return record(action, **kwargs)


def snapshot_fields(instance, fields):
    """The before/after helper: a dict of named field values, ready to diff."""
    if instance is None:
        return {}
    out = {}
    for name in fields:
        value = getattr(instance, name, None)
        out[name] = str(value) if value is not None else None
    return out


@dataclass(frozen=True)
class AuditVerification:
    ok: bool
    checked: int
    first_bad_sequence: int | None = None
    detail: str = ""

    @property
    def status(self):
        return "healthy" if self.ok else "broken"


def verify_chain(chain=None):
    """Walk the chain and confirm every link. Three ways it can fail.

    A gap in the sequence means a record was deleted; a mismatched
    ``previous_hash`` means one was inserted or reordered; a mismatched
    ``record_hash`` means one was edited in place.
    """
    chain = chain or AuditChain.objects.filter(key=chain_key()).first()
    if chain is None:
        return AuditVerification(True, 0)
    previous_hash = ""
    checked = 0
    for item in chain.records.select_related("chain").order_by("sequence"):
        if item.sequence != checked + 1:
            return AuditVerification(False, checked, item.sequence,
                                     "Sequence is not contiguous — a record is missing.")
        if item.previous_hash != previous_hash:
            return AuditVerification(False, checked, item.sequence,
                                     "Previous hash does not match its predecessor.")
        if item.record_hash != payload_hash(record_material(item)):
            return AuditVerification(False, checked, item.sequence,
                                     "Record hash does not verify — the row was edited.")
        previous_hash = item.record_hash
        checked += 1
    return AuditVerification(True, checked)
