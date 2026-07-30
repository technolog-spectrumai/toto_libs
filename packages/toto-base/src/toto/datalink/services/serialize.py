"""Turn a local row into a wire row, or refuse to.

Lifted from ``backup.services.backup_engine.serialize_object`` / ``_to_serializable``,
keeping what is genuinely good there and removing the one branch that quietly corrupts
data:

* **kept** — references as ``{"__ref__": true, "model": ..., "uid": ...}``, geometry as
  ``{"__geo__": true, "ewkt": ...}``, and canonical encoding so the bytes are stable.
* **removed** — the ``else`` that falls back to the raw ``*_id`` column when a target
  has no ``uid``. Cross-instance, an integer primary key names a *different row*, so
  every ``auth.User`` reference in a backup archive today points somewhere arbitrary.
  Here a reference is emitted only against a declared cross-instance identity;
  anything else raises, and ``validate_registry`` makes the raise unreachable.
* **added** — many-to-many, which ``obj._meta.fields`` silently excludes, so the backup
  engine loses ``Person.communities``, ``Community.senior_members``,
  ``ScheduledEvent.organizers`` and ``CommunityNewsPost.topics`` without a word.
* **added** — an ``omitted`` list per row: what this emitter chose not to send, and why.
  That is the mechanism behind "no silent degradation" — the receiver tallies the
  reasons and shows them, rather than the operator having to know what is missing.
"""
from __future__ import annotations

from typing import Any

from django.db.models.fields.files import FieldFile

from ..registry import FK_NULL, IDENTITY_UID, SyncPolicy, policy_for
from .canonical import row_checksum, sorted_refs

# Reasons that appear in a row's `omitted` list. Strings rather than an enum because
# they cross the wire and a peer one release ahead may send one this host has never
# heard of; an unknown reason must display, not crash.
OMIT_FILE_BYTES = "file_bytes_not_replicated"
OMIT_REFUSED_TARGET = "target_model_not_replicated"
OMIT_ABSENT_FIELD = "field_absent_on_this_build"


class DatalinkContractError(RuntimeError):
    """The registry allowed something the wire format cannot express.

    Unreachable if ``validate_registry`` passed, which is the point: this is the
    assertion that the static check is doing its job, not a runtime fallback.
    """


def identity_of(obj, policy: SyncPolicy) -> dict[str, Any]:
    """The cross-instance identity of a row: a uid, or the declared natural key."""
    if policy.identity == IDENTITY_UID:
        return {"uid": str(obj.uid)}
    natural: dict[str, Any] = {}
    for name in policy.natural_key:
        field = obj._meta.get_field(name)
        value = getattr(obj, name)
        if field.is_relation:
            # A natural key can include a reference — vault.VaultFile keys on
            # (bucket, key), events.EventInvite on (event, person). The component
            # travels as a reference so the receiver resolves it the same way.
            natural[name] = None if value is None else reference_to(value)
        else:
            natural[name] = value
    return {"natural": natural}


def reference_to(obj) -> dict[str, Any]:
    """Express a pointer to ``obj`` in terms the receiver can resolve.

    Raises rather than guessing. The guess is what the backup engine does.
    """
    label = obj._meta.label
    policy = policy_for(label)
    if policy is None or policy.refused:
        raise DatalinkContractError(
            f"cannot reference {label}: it has "
            f"{'no datalink policy' if policy is None else 'a refused policy'}, so it "
            f"has no cross-instance identity. Declare the referring field FK_NULL, or "
            f"register {label}."
        )
    if policy.identity == IDENTITY_UID:
        return {"__ref__": True, "model": label, "uid": str(obj.uid)}
    natural = {}
    for name in policy.natural_key:
        field = obj._meta.get_field(name)
        value = getattr(obj, name)
        natural[name] = None if (field.is_relation and value is None) else (
            reference_to(value) if field.is_relation else value
        )
    return {"__ref__": True, "model": label, "natural": natural}


def _scalar(value: Any) -> Any:
    """Convert a non-JSON-native value to something canonical_json can encode."""
    if value is None:
        return None
    try:
        from django.contrib.gis.geos import GEOSGeometry

        if isinstance(value, GEOSGeometry):
            # Tagged EWKT, carrying the SRID. The receiver drops it when its own build
            # has no geometry column, which is the GIS-on/GIS-off asymmetry the backup
            # engine already handles and we keep.
            return {"__geo__": True, "ewkt": value.ewkt}
    except ImportError:  # pragma: no cover - a GIS-less install
        pass
    if isinstance(value, FieldFile):
        # Never the path, and never the bytes. A path string is meaningless on the
        # receiver and is what the backup engine emits today, leaving a row pointing
        # at a file that is not there.
        raise DatalinkContractError(
            "a FileField reached the serializer; file bytes and storage paths are "
            "never replicated, so the field must not be in the policy's `fields`"
        )
    return value


def serialize_row(obj, policy: SyncPolicy, *, m2m_values=None) -> dict[str, Any]:
    """One wire row: identity, checksum, fields, m2m, and what was left out.

    ``m2m_values`` lets a caller pass values it already prefetched for the whole chunk,
    so serialising 500 rows does not issue 500 queries per m2m field.
    """
    meta = obj._meta
    fields: dict[str, Any] = {}
    omitted: list[dict[str, str]] = []

    for name in policy.fields:
        try:
            field = meta.get_field(name)
        except Exception:
            if name in policy.optional_fields:
                # A geometry column on a BUILD_GEO=0 host: absent from the model, not
                # merely null. Recorded so the receiver can say so rather than
                # wondering why a shape never arrived.
                omitted.append({"field": name, "reason": OMIT_ABSENT_FIELD})
                continue
            raise DatalinkContractError(f"{policy.model_label}.{name} is not a field")

        if field.is_relation and (field.many_to_one or field.one_to_one):
            if policy.ref_policy(name) == FK_NULL:
                omitted.append({"field": name, "reason": OMIT_REFUSED_TARGET})
                continue
            related = getattr(obj, name)
            fields[name] = None if related is None else reference_to(related)
            continue

        fields[name] = _scalar(getattr(obj, name))

    m2m: dict[str, list[dict[str, Any]]] = {}
    for name in policy.m2m:
        if m2m_values is not None and name in m2m_values:
            related_objects = m2m_values[name]
        else:
            related_objects = list(getattr(obj, name).all())
        m2m[name] = sorted_refs([reference_to(o) for o in related_objects])

    row: dict[str, Any] = {
        "identity": identity_of(obj, policy),
        "checksum": row_checksum(fields, m2m),
        "fields": fields,
    }
    if m2m:
        row["m2m"] = m2m
    if policy.timestamp_field:
        row["changed_at"] = getattr(obj, policy.timestamp_field, None)
    if omitted:
        row["omitted"] = omitted
    return row
