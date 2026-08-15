"""Find the local counterparts of a chunk's identities and references, in bulk.

Two jobs, both done **per chunk rather than per row**, because per-row is the shape that
makes replication unusable at size. ``backup.services.sync_service._resolve_reference``
issues one ``get()`` per foreign key per row; a page of 200 rows with three references
each is 600 queries, and the export side is no better — it has no ``select_related`` at
all, so it re-queries every FK it serialises.

Here a whole page is resolved in a fixed number of queries: one per distinct target
model for the references, one per model for the identities, plus one for the alias map.

The other thing this module owns is the **local projection**. To compare a local row
against the peer's, both must be described the same way — so the local side is projected
with the very same ``serialize_row`` the emitter uses. Reusing it rather than writing a
second projection is what guarantees the two checksums are comparable at all; a
parallel implementation that drifted would report phantom changes forever.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from django.apps import apps
from django.db.models import Q

from ..registry import IDENTITY_UID, SyncPolicy, policy_for
from .canonical import identity_hash
from .serialize import serialize_row


def local_projection(obj, policy: SyncPolicy) -> dict[str, Any]:
    """Describe a local row exactly as the emitter would describe it.

    The same function on both sides, on purpose — see the module docstring.
    """
    return serialize_row(obj, policy)


def local_checksum(obj, policy: SyncPolicy) -> str:
    return local_projection(obj, policy)["checksum"]


def identity_key(model_label: str, identity: Mapping[str, Any]) -> str:
    """The merge-base key for an identity, however it is expressed."""
    return identity_hash(model_label, identity)


def _identity_filter(policy: SyncPolicy, identity: Mapping[str, Any], resolved_refs) -> Q | None:
    """A Q that selects the one local row matching this identity, or None.

    None means "this identity cannot be looked up locally yet" — a natural key whose
    reference component has not arrived. That is not an error here; the caller turns it
    into an unresolved-reference conflict.
    """
    if policy.identity == IDENTITY_UID:
        return Q(uid=identity["uid"])

    lookup: dict[str, Any] = {}
    for name, value in (identity.get("natural") or {}).items():
        if isinstance(value, dict) and value.get("__ref__"):
            target = resolved_refs.get(_ref_key(value))
            if target is None:
                return None
            lookup[name] = target
        else:
            lookup[name] = value
    return Q(**lookup) if lookup else None


def _ref_key(ref: Mapping[str, Any]) -> str:
    """A hashable key for a reference, matching how the merge base keys identities."""
    label = ref["model"]
    if "uid" in ref:
        return identity_hash(label, {"uid": ref["uid"]})
    return identity_hash(label, {"natural": ref.get("natural", {})})


def collect_references(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[dict]]:
    """Every reference in a page, grouped by target model.

    Walks scalar fields, m2m lists, and the reference components of a natural-key
    identity — so a page is resolved in one pass rather than discovered row by row.
    """
    grouped: dict[str, dict[str, dict]] = {}

    def note(ref):
        if isinstance(ref, dict) and ref.get("__ref__"):
            grouped.setdefault(ref["model"], {})[_ref_key(ref)] = ref

    for row in rows:
        for value in (row.get("fields") or {}).values():
            note(value)
        for refs in (row.get("m2m") or {}).values():
            for ref in refs or ():
                note(ref)
        for value in (row.get("identity", {}).get("natural") or {}).values():
            note(value)

    return {label: list(refs.values()) for label, refs in grouped.items()}


def resolve_references(rows, *, peer=None) -> dict[str, Any]:
    """Map every reference in a page to a local object, in one query per model.

    A reference with no local counterpart is simply absent from the result; the caller
    reports it rather than guessing, which is the whole difference from the engine this
    replaces.
    """
    resolved: dict[str, Any] = {}
    grouped = collect_references(rows)
    aliases = _alias_map(peer, grouped.keys()) if peer is not None else {}

    for label, refs in grouped.items():
        policy = policy_for(label)
        if policy is None or policy.refused:
            # Unreachable if validate_registry passed — the emitter would have raised
            # rather than sending this. Skipped rather than crashed so one malformed
            # payload from a peer cannot take the receiver down.
            continue
        model = apps.get_model(label)

        # An operator-approved alias wins: it says "the peer's identity X is our row Y",
        # which is how two instances that never synced reconcile differing uids.
        wanted = []
        for ref in refs:
            key = _ref_key(ref)
            aliased = aliases.get(key)
            if aliased is not None:
                resolved[key] = aliased
            else:
                wanted.append((key, ref))
        if not wanted:
            continue

        if policy.identity == IDENTITY_UID:
            uids = [ref["uid"] for _key, ref in wanted]
            by_uid = {str(o.uid): o for o in model._default_manager.filter(uid__in=uids)}
            for key, ref in wanted:
                found = by_uid.get(str(ref["uid"]))
                if found is not None:
                    resolved[key] = found
        else:
            # A composite natural key cannot use a plain __in, so it is one OR-chain —
            # still a single query for the whole page.
            query = Q()
            any_term = False
            for _key, ref in wanted:
                term = _identity_filter(policy, {"natural": ref.get("natural", {})}, resolved)
                if term is not None:
                    query |= term
                    any_term = True
            if any_term:
                for obj in model._default_manager.filter(query):
                    resolved[_ref_key(_reference_of(obj, policy))] = obj

    return resolved


def _reference_of(obj, policy: SyncPolicy) -> dict[str, Any]:
    """The reference a local object would be described by, for keying the result."""
    from .serialize import reference_to

    return reference_to(obj)


def _alias_map(peer, model_labels) -> dict[str, Any]:
    """Operator-approved identity equivalences for this peer, in one query."""
    from ..models import DatalinkIdentityMap

    labels = list(model_labels)
    if not labels:
        return {}
    out: dict[str, Any] = {}
    rows = DatalinkIdentityMap.objects.filter(peer=peer, model_label__in=labels)
    by_label: dict[str, dict[str, str]] = {}
    for row in rows:
        by_label.setdefault(row.model_label, {})[row.peer_identity_hash] = row.local_pk
    for label, mapping in by_label.items():
        try:
            model = apps.get_model(label)
        except LookupError:
            continue
        objects = {str(o.pk): o for o in model._default_manager.filter(pk__in=mapping.values())}
        for peer_hash, local_pk in mapping.items():
            found = objects.get(str(local_pk))
            if found is not None:
                out[peer_hash] = found
    return out


def resolve_identities(policy: SyncPolicy, rows, *, resolved_refs=None) -> dict[str, Any]:
    """Map each row's identity to its existing local object, in one query.

    Absent from the result means "not here", which the decision table reads as an insert
    (no base) or a local delete (base present) — a distinction the base is what makes
    possible.
    """
    resolved_refs = resolved_refs or {}
    model = apps.get_model(policy.model_label)
    keys: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        identity = row.get("identity") or {}
        keys[identity_key(policy.model_label, identity)] = identity

    if not keys:
        return {}

    queryset = model._default_manager.all()
    relations = [
        name for name in policy.fields
        if _is_forward_relation(model, name)
    ]
    if relations:
        # Needed, not merely faster: the local projection reads each reference target's
        # identity to build its checksum, so without this it is a query per row per FK.
        queryset = queryset.select_related(*relations)
    if policy.m2m:
        queryset = queryset.prefetch_related(*policy.m2m)

    if policy.identity == IDENTITY_UID:
        uids = [identity["uid"] for identity in keys.values() if "uid" in identity]
        found = queryset.filter(uid__in=uids) if uids else model._default_manager.none()
    else:
        query = Q()
        any_term = False
        for identity in keys.values():
            term = _identity_filter(policy, identity, resolved_refs)
            if term is not None:
                query |= term
                any_term = True
        found = queryset.filter(query) if any_term else model._default_manager.none()

    out: dict[str, Any] = {}
    for obj in found:
        from .serialize import identity_of

        out[identity_key(policy.model_label, identity_of(obj, policy))] = obj
    return out


def _is_forward_relation(model, name: str) -> bool:
    try:
        field = model._meta.get_field(name)
    except Exception:
        return False
    return bool(field.is_relation and (field.many_to_one or field.one_to_one))


def load_merge_bases(peer, policy: SyncPolicy, identity_hashes) -> dict[str, Any]:
    """The merge bases for a page, in one indexed query."""
    from ..models import DatalinkMergeBase

    hashes = list(identity_hashes)
    if not hashes:
        return {}
    return {
        base.identity_hash: base
        for base in DatalinkMergeBase.objects.filter(
            peer=peer, model_label=policy.model_label, identity_hash__in=hashes,
        ).only(
            "identity_hash", "peer_checksum", "local_checksum", "peer_changed_at", "local_pk",
        )
    }
