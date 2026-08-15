"""What datalink may replicate, declared per model and validated before use.

datalink converges two toto instances by pulling rows from a peer's read API. This
module is the contract for *which* rows, *which fields*, and *how a row on one
instance is recognised as the same row on the other*. Nothing is replicated by
default: a model is either declared with a policy or declared refused, and
``validate_registry`` fails the build if it is neither.

Declarations live in per-app ``toto/<app>/datalink_policies.py`` and are collected by
``autodiscover_plugins("datalink_policies")`` from ``DatalinkConfig.ready()``. That
gets the uninstalled-app problem right for free — a policy for an app this host does
not install is simply never imported. Contrast ``settings.APPS_TO_SYNC``, which names
app *labels* a host must keep in sync by hand: one stale label makes
``backup_engine.iter_models()`` raise ``LookupError`` and breaks *every* backup and
restore path, which is why zenobia's settings carry a per-flag guard around each one.

This replaces a deleted predecessor. Five ``sync_adapters.py`` files in the tree
still import ``toto.noosphere``, a package that does not exist; each declared a
``model`` plus an ``allowed_fields`` list. That curation — no PKs, no timestamps, no
owner columns, nothing implicit — is the good idea here and is preserved. What it
lacked was any way to express a *reference*, so its lists had to omit every FK and
M2M; those are declared explicitly now.

Design rules worth knowing before editing a policy:

* **Identity is never a primary key.** Auto-increment ids and per-instance UUID pks
  mean different rows on different instances. Identity is ``DomainEntity.uid`` where
  it exists (``core/domain.py`` — its docstring calls it "cross-system identity") or
  a declared natural key backed by a real unique constraint.
* **A reference datalink cannot express, it refuses to emit.** ``backup_engine``
  falls back to the raw integer pk for any FK whose target has no ``uid``, which
  silently points at a different row on the receiver. Here, every FK must either
  resolve to a registered identity or be declared ``FK_NULL``; ``validate_registry``
  rejects anything else *at startup*, before a byte moves.
* **``auth.User`` is refused, and that is load-bearing.** datalink never creates,
  updates or deletes an account — identity is federation's job. Because the refusal
  lives here, any model that still points at a User fails validation rather than
  quietly shipping a broken reference.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Mapping

# --- identity strategies ---------------------------------------------------
IDENTITY_UID = "uid"          # DomainEntity.uid — a UUID that means the same thing on both sides
IDENTITY_NATURAL = "natural"  # a declared tuple of fields backed by a unique constraint
IDENTITY_REFUSE = "refuse"    # declared un-replicable, with a reason

# --- foreign-key policies --------------------------------------------------
FK_REQUIRE = "require"  # target must be registered and not refused; unresolvable -> conflict
FK_NULL = "null"        # deliberately dropped; target is refused; the field must be nullable

# --- conflict policies -----------------------------------------------------
CONFLICT_NEWEST = "newest_wins"      # three-way merge base, timestamp tiebreak where one exists
CONFLICT_INSERT_ONLY = "insert_only"  # create when absent; never update an existing local row

# Models whose save() has side effects beyond the row. Not discoverable by
# inspection, so it is declared: socialhub.ReferenceRequest.save() activates a User
# and creates a Person when its status becomes accepted, which datalink must never
# do. validate_registry insists these are refused.
SAVE_SIDE_EFFECT_MODELS = frozenset({"socialhub.ReferenceRequest"})

# Apps whose models must all be accounted for — registered or refused. A model added
# to one of these next year fails validation until somebody decides about it, which
# is the whole point: silence must not mean "replicate it".
DATALINK_APPS = (
    "auth",
    "core",
    "api",
    "gervazy",
    "people",
    "locations",
    "socialhub",
    "events",
    "vault",
    "quota",
    "backup",
    "datalink",
)

# Stage keys, in the order a run walks them. Intra-stage model order lives in the
# policies themselves (see stage_models); this is the dependency spine.
#
# It is a library constant on purpose. backup_engine reads BACKUP_MODEL_ORDER from
# host settings and *no host sets it*, so that engine has no dependency ordering at
# all — a restore succeeds only by luck. Ordering here is code, and rule 9 of
# validate_registry proves every declared FK agrees with it.
STAGE_INFRA = "infra"
STAGE_PLACES = "places"
STAGE_PEOPLE = "people"
STAGE_MAPS = "maps"
STAGE_COMMUNITIES = "communities"
STAGE_MEMBERSHIP = "membership"
STAGE_CONTENT = "content"
STAGE_EVENTS = "events"

STAGES: tuple[str, ...] = (
    STAGE_INFRA,
    STAGE_PLACES,
    STAGE_PEOPLE,
    STAGE_MAPS,
    STAGE_COMMUNITIES,
    STAGE_MEMBERSHIP,
    STAGE_CONTENT,
    STAGE_EVENTS,
)

STAGE_TITLES: Mapping[str, str] = {
    STAGE_INFRA: "Federations",
    STAGE_PLACES: "Places",
    STAGE_PEOPLE: "People",
    STAGE_MAPS: "Map layers",
    STAGE_COMMUNITIES: "Communities",
    STAGE_MEMBERSHIP: "Memberships",
    STAGE_CONTENT: "News and charters",
    STAGE_EVENTS: "Events",
}


@dataclass(frozen=True)
class SyncPolicy:
    """One model's replication contract.

    ``model_label`` is a string rather than a model class so a policy module can be
    imported before the app registry is ready and so declaring a policy never
    creates an import edge between apps.
    """

    model_label: str
    stage: str
    identity: str

    # Required when identity is IDENTITY_NATURAL: the field names forming the key.
    natural_key: tuple[str, ...] = ()

    # Other unique constraints a write could violate even when the identity matches.
    # These are what turn "the same person under a different uid" from an
    # IntegrityError into a reviewable conflict — see services/decide.py.
    unique_guards: tuple[tuple[str, ...], ...] = ()

    # The allowlist: scalar and forward-relation field NAMES (not attnames).
    fields: tuple[str, ...] = ()

    # Fields that legitimately do not exist on some builds. The only real case is
    # GeoDjango geometry: on a BUILD_GEO=0 host, toto.locations is migrated from
    # migrations_nogis and the geometry columns are absent from the model entirely.
    optional_fields: tuple[str, ...] = ()

    m2m: tuple[str, ...] = ()
    m2m_stage: str | None = None  # defaults to `stage`

    # field name -> FK_REQUIRE | FK_NULL. A field absent from this map defaults to
    # FK_REQUIRE, so dropping a reference is always an explicit act.
    refs: Mapping[str, str] = field(default_factory=dict)

    # A nullable self-FK, inserted NULL and patched once its target exists. This is
    # the only kind of intra-stage forward reference the engine accepts.
    parent_field: str | None = None

    conflict: str = CONFLICT_NEWEST

    # An auto_now field usable to break a both-changed tie. None means there is no
    # honest tiebreak for this model, and the engine must report rather than guess.
    timestamp_field: str | None = None

    # May the writer use bulk_create/bulk_update, skipping save()? False keeps a
    # model's save() overrides running, which matters where save() derives a field
    # the peer also sent (locations.Address re-derives lat/lon from geometry).
    bulk_safe: bool = False

    refuse_reason: str = ""
    notes: str = ""

    @property
    def refused(self) -> bool:
        return self.identity == IDENTITY_REFUSE

    @property
    def effective_m2m_stage(self) -> str:
        return self.m2m_stage or self.stage

    def ref_policy(self, field_name: str) -> str:
        return self.refs.get(field_name, FK_REQUIRE)


# --- the registry itself ---------------------------------------------------
# Module-level because policy modules register at import time, exactly as the
# suite's other plugin registries do (VaultPlayPlugin, FileServicePlugin, the
# connector registry). Keyed by model_label so a duplicate declaration is caught
# rather than silently overwriting.
_REGISTRY: dict[str, SyncPolicy] = {}

# Labels declared more than once. Recorded rather than raised, because a policy
# module must not be able to break `manage.py migrate`; validate_registry reports it.
_DUPLICATES: list[str] = []

# Declaration order, which IS the intra-stage write order. A policy module reads
# top-to-bottom in dependency order (Address before Territory, RouteChain before
# Route), and that order has to survive into the engine — sorting by model_label
# instead would put Route before RouteChain and write a route before its chain.
_ORDER: dict[str, int] = {}


def register(policy: SyncPolicy) -> SyncPolicy:
    """Declare a policy. A duplicate label is reported by validate_registry."""
    if policy.model_label in _REGISTRY:
        _DUPLICATES.append(policy.model_label)
        return policy
    _REGISTRY[policy.model_label] = policy
    _ORDER[policy.model_label] = len(_ORDER)
    return policy


def declaration_index(model_label: str) -> int:
    """Where a model sits in its stage's write order. Lower is written first."""
    return _ORDER.get(model_label, len(_ORDER))


def load_registry() -> dict[str, SyncPolicy]:
    """Every declared policy. Import-order independent; call after app loading."""
    return dict(_REGISTRY)


def duplicate_labels() -> list[str]:
    return list(_DUPLICATES)


def clear_registry() -> None:
    """Tests only: drop every declaration so a module can be re-imported."""
    _REGISTRY.clear()
    _DUPLICATES.clear()
    _ORDER.clear()


def replicated_policies() -> list[SyncPolicy]:
    """Policies that actually move rows, in stage then declaration order."""
    stage_of = {stage: i for i, stage in enumerate(STAGES)}
    return sorted(
        (p for p in _REGISTRY.values() if not p.refused),
        key=lambda p: (stage_of.get(p.stage, len(STAGES)), declaration_index(p.model_label)),
    )


def stage_models(stage: str) -> list[SyncPolicy]:
    """The policies a stage writes, in the order it must write them.

    Intra-stage order is declaration order, which is why the policy modules read
    top-to-bottom in dependency order. Rule 9 proves that order actually satisfies
    every declared reference rather than trusting the author to have got it right.
    """
    return [p for p in replicated_policies() if p.stage == stage]


def policy_for(model_label: str) -> SyncPolicy | None:
    return _REGISTRY.get(model_label)


def registry_digest(registry: Mapping[str, SyncPolicy] | None = None) -> str:
    """A stable fingerprint of the replication contract.

    Two peers compare digests at preflight and refuse to run when they differ: the
    receiver writes the peer's rows straight into its own tables, so a field the two
    sides disagree about is silent bad data with no round trip to catch it. Only the
    parts that change the wire are hashed — a comment or a refuse_reason must not
    make two compatible instances refuse each other.
    """
    reg = _REGISTRY if registry is None else registry
    payload = [
        {
            "model": p.model_label,
            "stage": p.stage,
            "identity": p.identity,
            "natural_key": list(p.natural_key),
            "fields": sorted(p.fields),
            "m2m": sorted(p.m2m),
            "m2m_stage": p.effective_m2m_stage if not p.refused else None,
            "refs": {k: v for k, v in sorted(p.refs.items())},
            "conflict": p.conflict,
        }
        for p in sorted(reg.values(), key=lambda p: p.model_label)
        if not p.refused
    ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
