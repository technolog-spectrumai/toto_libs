"""Prove the replication contract is sound before anything reads it.

``validate_registry`` is a pure function: it takes the declared policies, asks the app
registry what the models actually look like, and returns a list of human-readable
problems. No database, no network, no writes. It follows the shape of
``sql_neo4j_sync.loader.validate_configs`` — collect, then check, then return strings
— because that is the pattern ``ravioli_check`` already exposes as a command.

It is called from three places, deliberately at three different severities:

* ``DatalinkConfig.ready()`` logs at ERROR and carries on. A broken policy module must
  never stop ``manage.py migrate``, or a bad declaration becomes unfixable.
* ``manage.py datalink_check`` prints and exits non-zero, so CI and the clean-env
  gates fail on it.
* the run's preflight refuses to start. That is the one that actually protects data.

The rules are numbered in the plan; the numbering is kept here so a failure message
can be traced back to a decision.
"""
from __future__ import annotations

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist

from .registry import (
    DATALINK_APPS,
    declaration_index,
    FK_NULL,
    FK_REQUIRE,
    IDENTITY_NATURAL,
    IDENTITY_REFUSE,
    IDENTITY_UID,
    SAVE_SIDE_EFFECT_MODELS,
    STAGES,
    CONFLICT_NEWEST,
    SyncPolicy,
    duplicate_labels,
    load_registry,
)

_IDENTITIES = {IDENTITY_UID, IDENTITY_NATURAL, IDENTITY_REFUSE}


def _stage_index(stage: str) -> int:
    try:
        return STAGES.index(stage)
    except ValueError:
        return -1


def _unique_field_names(model) -> set[str]:
    return {f.name for f in model._meta.get_fields()
            if getattr(f, "concrete", False) and getattr(f, "unique", False)}


def _unique_tuples(model) -> set[frozenset[str]]:
    """Every unique constraint on the model, as sets of field names.

    Covers the three ways Django expresses one: a ``unique=True`` field, the legacy
    ``unique_together``, and a modern ``UniqueConstraint``. A natural key is only
    trustworthy if it matches one of these — otherwise two different rows could
    share it and the receiver would merge two entities into one.
    """
    out: set[frozenset[str]] = {frozenset({name}) for name in _unique_field_names(model)}
    for group in getattr(model._meta, "unique_together", ()) or ():
        out.add(frozenset(group))
    for constraint in getattr(model._meta, "constraints", ()) or ():
        names = getattr(constraint, "fields", None)
        # A partial constraint (one with a condition) does not guarantee global
        # uniqueness, so it cannot serve as an identity.
        if names and getattr(constraint, "condition", None) is None:
            out.add(frozenset(names))
    return out


def validate_registry(registry=None) -> list[str]:  # noqa: C901 - a checklist, kept flat on purpose
    """Return a list of problems. Empty means the contract is sound."""
    reg = load_registry() if registry is None else dict(registry)
    errors: list[str] = []

    # Rule 2 — a label declared twice means two modules disagree about a model.
    for label in duplicate_labels():
        errors.append(f"{label}: declared more than once")

    resolved: dict[str, tuple[SyncPolicy, object]] = {}
    for label, policy in reg.items():
        if policy.identity not in _IDENTITIES:
            errors.append(f"{label}: unknown identity {policy.identity!r}")
            continue
        try:
            model = apps.get_model(label)
        except LookupError:
            # Rule 1 — an app this host does not install is not an error. The whole
            # point of per-app policy modules is that they are simply not imported;
            # a label that survives without its app is a stale declaration, which we
            # skip rather than fail on so a lean host still validates.
            continue
        except ValueError as exc:
            errors.append(f"{label}: not a valid model label ({exc})")
            continue
        resolved[label] = (policy, model)

    for label, (policy, model) in resolved.items():
        # Rule 12 — save() side effects are not discoverable, so they are declared.
        if label in SAVE_SIDE_EFFECT_MODELS and not policy.refused:
            errors.append(
                f"{label}: must be refused — its save() has side effects beyond the "
                f"row (it activates a User and creates a Person), which datalink "
                f"must never trigger"
            )

        if policy.refused:
            if not policy.refuse_reason:
                errors.append(f"{label}: refused without a reason")
            continue

        if policy.stage not in STAGES:
            errors.append(f"{label}: unknown stage {policy.stage!r}")
        if policy.m2m_stage is not None and policy.m2m_stage not in STAGES:
            errors.append(f"{label}: unknown m2m_stage {policy.m2m_stage!r}")

        # Rule 5/6 — the identity must be real, not aspirational.
        if policy.identity == IDENTITY_UID:
            if "uid" not in _unique_field_names(model):
                errors.append(f"{label}: identity 'uid' but no unique field named uid")
        else:
            if not policy.natural_key:
                errors.append(f"{label}: identity 'natural' but natural_key is empty")
            elif frozenset(policy.natural_key) not in _unique_tuples(model):
                errors.append(
                    f"{label}: natural_key {policy.natural_key} is not backed by a "
                    f"unique constraint — two different rows could share it, and the "
                    f"receiver would merge two entities into one"
                )

        # Rule 3 — every declared field must exist, be concrete, and not be derived.
        declared = set(policy.fields)
        for name in policy.fields:
            try:
                f = model._meta.get_field(name)
            except FieldDoesNotExist:
                if name in policy.optional_fields:
                    continue  # a geometry column absent on a BUILD_GEO=0 build
                errors.append(f"{label}.{name}: declared in fields but not a field")
                continue
            if not getattr(f, "concrete", False):
                errors.append(f"{label}.{name}: not a concrete field")
            if f.primary_key:
                errors.append(
                    f"{label}.{name}: primary keys are per-instance and must never travel"
                )
            if f.many_to_many:
                errors.append(f"{label}.{name}: many-to-many belongs in m2m, not fields")
            if getattr(f, "auto_now", False) or getattr(f, "auto_now_add", False):
                errors.append(
                    f"{label}.{name}: auto_now/auto_now_add is set by the receiver's "
                    f"own clock and cannot be replicated"
                )
            if name == "uid":
                errors.append(f"{label}.uid: the identity travels separately, not as a field")

        # Rule 4 — m2m names must really be m2m.
        m2m_names = {f.name for f in model._meta.many_to_many}
        for name in policy.m2m:
            if name not in m2m_names:
                errors.append(f"{label}.{name}: declared in m2m but not a many-to-many field")

        # Rules 7 and 8 — the reference contract. This is where "datalink never
        # touches users" stops being a promise and becomes a check: auth.User is
        # declared refused, so any FK pointing at it must be explicitly dropped, and
        # a non-nullable one makes the whole model un-replicable.
        for name in policy.fields:
            try:
                f = model._meta.get_field(name)
            except FieldDoesNotExist:
                continue
            if not (f.is_relation and (f.many_to_one or f.one_to_one)):
                continue
            target_label = f.related_model._meta.label
            target = reg.get(target_label)
            wanted = policy.ref_policy(name)

            if wanted == FK_NULL:
                if not f.null:
                    errors.append(
                        f"{label}.{name}: declared FK_NULL but the column is NOT NULL — "
                        f"the reference cannot be dropped, so {label} itself must be refused"
                    )
                continue

            if target is None:
                errors.append(
                    f"{label}.{name} -> {target_label}: target has no policy. Register "
                    f"it, or declare this field FK_NULL. datalink refuses to emit a "
                    f"reference it cannot express as a cross-instance identity."
                )
            elif target.refused:
                errors.append(
                    f"{label}.{name} -> {target_label}: target is refused"
                    f"{' (' + target.refuse_reason + ')' if target.refuse_reason else ''}."
                    f" Declare this field FK_NULL"
                    + ("" if f.null else ", but the column is NOT NULL so refuse "
                                        f"{label} instead")
                )
            else:
                # Rule 9 — ordering. A target written after this row cannot be resolved
                # when the row lands. Within a stage, "after" means declared later:
                # stage_models() returns policies in declaration order, so a policy
                # module reading top-to-bottom in dependency order is the ordering.
                # This is what backup_engine lacks entirely — it reads
                # BACKUP_MODEL_ORDER from host settings and no host sets it.
                here, there = _stage_index(policy.stage), _stage_index(target.stage)
                if there > here:
                    errors.append(
                        f"{label}.{name} -> {target_label}: target is written in a later "
                        f"stage ({target.stage} after {policy.stage})"
                    )
                elif target_label == label:
                    if name != policy.parent_field:
                        errors.append(
                            f"{label}.{name}: a self-reference must be declared as "
                            f"parent_field so the engine defers it"
                        )
                elif there == here and declaration_index(target_label) > declaration_index(label):
                    errors.append(
                        f"{label}.{name} -> {target_label}: both are in stage "
                        f"{policy.stage}, but {target_label} is declared AFTER {label}, so "
                        f"it will not exist yet. Move its register() call above "
                        f"{label}'s in the policy module."
                    )

        # parent_field must be a nullable self-FK, or the deferred buffer cannot work.
        if policy.parent_field:
            try:
                pf = model._meta.get_field(policy.parent_field)
            except FieldDoesNotExist:
                errors.append(f"{label}: parent_field {policy.parent_field!r} is not a field")
            else:
                if not (pf.is_relation and pf.related_model._meta.label == label):
                    errors.append(
                        f"{label}.{policy.parent_field}: parent_field must point at "
                        f"{label} itself"
                    )
                elif not pf.null:
                    errors.append(
                        f"{label}.{policy.parent_field}: parent_field must be nullable — "
                        f"the engine inserts NULL and patches it once the target exists"
                    )
                elif policy.parent_field not in declared:
                    errors.append(
                        f"{label}.{policy.parent_field}: parent_field must also be listed "
                        f"in fields"
                    )

        # Rule 9 (m2m half) — both endpoints must exist before the link is written.
        for name in policy.m2m:
            if name not in m2m_names:
                continue
            target_label = model._meta.get_field(name).related_model._meta.label
            target = reg.get(target_label)
            if target is None or target.refused:
                errors.append(
                    f"{label}.{name} -> {target_label}: m2m target is "
                    f"{'unregistered' if target is None else 'refused'}. An m2m to a "
                    f"model that does not replicate cannot be written."
                )
                continue
            m2m_at = _stage_index(policy.effective_m2m_stage)
            if _stage_index(target.stage) > m2m_at:
                errors.append(
                    f"{label}.{name} -> {target_label}: m2m written in "
                    f"{policy.effective_m2m_stage} but the target lands later "
                    f"({target.stage})"
                )
            if _stage_index(policy.stage) > m2m_at:
                errors.append(
                    f"{label}.{name}: m2m_stage {policy.effective_m2m_stage} is before "
                    f"the row's own stage {policy.stage}"
                )

        # Rule 11 — a declared tiebreak must exist and be a datetime.
        if policy.timestamp_field:
            try:
                tf = model._meta.get_field(policy.timestamp_field)
            except FieldDoesNotExist:
                errors.append(
                    f"{label}: timestamp_field {policy.timestamp_field!r} is not a field"
                )
            else:
                if tf.get_internal_type() != "DateTimeField":
                    errors.append(
                        f"{label}.{policy.timestamp_field}: timestamp_field must be a "
                        f"DateTimeField"
                    )
                elif not getattr(tf, "auto_now", False):
                    errors.append(
                        f"{label}.{policy.timestamp_field}: only an auto_now field is a "
                        f"modification stamp. A creation stamp (auto_now_add) cannot "
                        f"arbitrate an edit, and using one would silently pick a winner "
                        f"at random."
                    )
        elif policy.conflict == CONFLICT_NEWEST:
            # Not an error — most of the scope genuinely has no modification stamp,
            # which is exactly why the merge base exists. Recorded as a note by
            # describe_registry() so it is visible rather than assumed.
            pass

        # unique_guards must name real constraints, or a collision the engine expects
        # to catch would surface as an IntegrityError mid-chunk instead.
        for guard in policy.unique_guards:
            if frozenset(guard) not in _unique_tuples(model):
                errors.append(
                    f"{label}: unique_guard {tuple(guard)} is not a unique constraint"
                )

    # Rule 10 — completeness. Silence must not mean "replicate it".
    for app_label in DATALINK_APPS:
        try:
            config = apps.get_app_config(app_label)
        except LookupError:
            continue  # the host does not install this app
        for model in config.get_models():
            label = model._meta.label
            if label not in reg:
                errors.append(
                    f"{label}: no datalink policy. Every model of {app_label} must be "
                    f"registered or explicitly refused — add one to "
                    f"toto/{app_label}/datalink_policies.py"
                )

    return errors


def describe_registry(registry=None) -> list[str]:
    """Advisory notes: true statements worth seeing, that are not errors."""
    reg = load_registry() if registry is None else dict(registry)
    notes: list[str] = []
    no_stamp = sorted(
        p.model_label for p in reg.values()
        if not p.refused and p.conflict == CONFLICT_NEWEST and not p.timestamp_field
    )
    if no_stamp:
        notes.append(
            "no modification timestamp, so a both-changed conflict is reported rather "
            "than auto-resolved: " + ", ".join(no_stamp)
        )
    dropped = sorted(
        f"{p.model_label}.{name}"
        for p in reg.values() if not p.refused
        for name, policy in p.refs.items() if policy == FK_NULL
    )
    if dropped:
        notes.append("references deliberately dropped: " + ", ".join(dropped))
    return notes
