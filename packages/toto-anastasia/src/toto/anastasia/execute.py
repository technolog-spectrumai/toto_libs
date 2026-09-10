"""Submitting one heavy job into a Gear the user chose.

The three-function shape every run table on this platform uses — ``submit``
makes the row, the backend starts the runner, ``finish``/``fail`` close it —
so there is exactly one place an execution can start and exactly one where it
can end badly.

**Nothing here runs inline.** A caller submits and polls. That is the decision
aralia's ``dispatch.py`` documents (a render in the request is a blocked socket
pretending to be a spinner), and it is what makes a killed worker recoverable:
the row outlives the process.

The caller supplies an OPERATION NAME and DECLARED PARAMETERS. It cannot supply
an image, a mount, a flag, a capability or a command — the vocabulary in
:mod:`toto.anastasia.families` has no word for those.
"""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from . import choices, families, services
from .limits import Limits, LimitsError
from .models import ComputeLease, Execution, CapsuleEvent
from .runtime import RuntimeUnavailable, get_backend

log = logging.getLogger("toto.anastasia.execute")


class CannotExecute(ValidationError):
    """No way to run this right now. The message is for a user."""

    def __init__(self, message, code: str = ""):
        super().__init__(message)
        self.refusal_code = code


def _audit(action: str, *, lease=None, execution=None, actor=None,
           success: bool = True, **metadata) -> None:
    """One line in the append-only trail, for the four things that matter.

    WHAT IS RECORDED and what deliberately is not. This is the trail an
    operator reads after an incident, so it carries WHO asked, WHICH Gear,
    WHICH operation, WHAT isolation it ran under and HOW it ended. It carries
    no parameters and no output: the job's inputs are the user's data, and an
    audit trail that copied them would become a second place their documents
    live.

    NEVER a key containing "token": `toto.audit` redacts those, and a metadata
    key that silently became [REDACTED] would make the trail lie about itself.

    Failures here are swallowed. An audit backend that is down must not turn a
    working compute tier into a broken one — the trail is evidence, not a
    dependency of the thing it observes.
    """
    try:
        from toto.audit import record

        payload = {k: v for k, v in metadata.items() if v not in (None, "")}
        if lease is not None:
            payload["gear"] = str(lease.uuid)
        if execution is not None:
            payload.setdefault("operation", execution.operation)
            payload.setdefault("family", execution.family)
            payload["limits"] = execution.limits.as_dict()
        record(
            action,
            app_label="anastasia",
            obj=execution if execution is not None else lease,
            description=(f"{action} {execution.operation}"
                         if execution is not None else action),
            actor_user=actor,
            success=success,
            metadata=payload,
        )
    except Exception:  # noqa: BLE001 — evidence, never a dependency
        log.exception("anastasia: could not write an audit record for %s", action)


def _resolve_limits(operation, requested) -> Limits:
    """What this execution books against its Gear.

    Defaults come from the family; a caller may ask for less (a small OCR job
    need not book a media-sized runner) but never for more than its Gear holds,
    which the admission check below enforces.
    """
    if requested is None:
        return operation.family.default_limits
    try:
        wanted = (requested if isinstance(requested, Limits)
                  else Limits.from_mapping(requested))
    except LimitsError as exc:
        raise CannotExecute(str(exc)) from exc
    # Any dimension left at zero means "use the family default for it", so a
    # caller can raise RAM alone without having to restate the other three.
    default = operation.family.default_limits
    return Limits(
        wanted.cpu_millicores or default.cpu_millicores,
        wanted.ram_mb or default.ram_mb,
        wanted.scratch_mb or default.scratch_mb,
        wanted.pids or default.pids,
    )


def submit(*, lease: ComputeLease, operation: str, params: dict | None = None,
           payload=None, limits=None, timeout: int | None = None,
           subject_label: str = "", subject_id="", requested_by=None) -> Execution:
    """Start a job in this Gear, or refuse with a sentence.

    ``payload`` is the staged input — bytes the caller has already gathered
    from the Vault through its own permissions. Anastasia never reaches into
    a vault; it receives what the caller decided this job may see.

    EVERY REFUSAL IS AUDITED, and this wrapper is why. There are ten places
    below that raise `CannotExecute` — a closed lease, a Gear that is full, a
    job too big for its Gear, an exhausted quota, arrears, a runtime that will
    not answer — and instrumenting each one would mean the eleventh, added
    later, is the one nobody records. Catching at the boundary makes "a refused
    job leaves a trail" structural rather than a habit.
    """
    try:
        return _submit(
            lease=lease, operation=operation, params=params, payload=payload,
            limits=limits, timeout=timeout, subject_label=subject_label,
            subject_id=subject_id, requested_by=requested_by)
    except CannotExecute as exc:
        _audit("anastasia.job.refuse", lease=lease, actor=requested_by,
               success=False, operation=operation,
               refusal_code=getattr(exc, "refusal_code", "") or "",
               # The sentence the user was actually shown. An operator asking
               # "why could they not run this" wants the words, not a code.
               error="; ".join(exc.messages)[:400])
        raise


def _submit(*, lease: ComputeLease, operation: str, params: dict | None = None,
            payload=None, limits=None, timeout: int | None = None,
            subject_label: str = "", subject_id="", requested_by=None) -> Execution:
    op = families.operation(operation)          # raises ParamError by name
    try:
        clean_params = op.clean(params)
        clean_timeout = op.clean_timeout(timeout)
    except families.ParamError as exc:
        raise CannotExecute(str(exc)) from exc

    wanted = _resolve_limits(op, limits)

    if not lease.is_open():
        raise CannotExecute(
            "That Gear's reservation has ended. Reserve a new one to keep "
            "working.", services.LEASE_CLOSED)

    runtime = services.runtime_for(lease)
    state = services.derive_state(runtime)
    if state not in choices.ACCEPTING:
        raise CannotExecute(
            _not_accepting_sentence(lease, state), services.NOT_MOUNTED)

    # The rate limit, at the ONE door every caller comes through — texlab,
    # memo, ocr, manta and dracena all reach a runner via this function, so a
    # guard here cannot be gone round by adding a sixth caller.
    #
    # Checked, recorded, and NOT charged. Each caller already prices its own
    # action (texlab.compile, memo.pdf, ...); pricing the execution as well
    # would charge twice for one job. What compute genuinely costs is the
    # RESERVATION, which is a levy over time and belongs to toto.tax — see
    # metrics.py and the TODO in portal/anastasia.md.
    #
    # Checked after the Gear is known to be accepting so a refusal names the
    # useful reason first, and before the row is created so a rejected
    # submission leaves nothing behind.
    if requested_by is not None and getattr(requested_by, "pk", None):
        from toto.quota import InArrears, QuotaExceeded, check_quota

        from .models import AnastasiaQuotaPolicy
        try:
            check_quota(AnastasiaQuotaPolicy, "anastasia.execution", 1,
                        requested_by)
        except QuotaExceeded as exc:
            raise CannotExecute(str(exc), services.QUOTA_EXCEEDED) from exc
        except InArrears as exc:
            # check_quota raises this BEFORE it looks for a policy, so it fires
            # whether or not anastasia has one — catching only QuotaExceeded
            # let it escape submit() as an unhandled exception, which is a 500
            # on a page whose whole job is to refuse in a sentence.
            raise CannotExecute(
                str(exc) or "This account is behind on a recurring fee, so it "
                "cannot start new work until the balance clears.",
                services.IN_ARREARS) from exc

    with transaction.atomic():
        # Lock the lease row so two submissions into one Gear cannot both read
        # the same headroom — the pool race, one level down.
        locked = ComputeLease.objects.select_for_update().get(pk=lease.pk)

        # Two different refusals wear the same shape, and telling a user the
        # wrong one is worse than telling them nothing: "the Gear is busy" sent
        # to somebody whose Gear is idle reads as a bug in the platform. So ask
        # the bigger question first — would this EVER fit in this Gear?
        if not wanted.fits_in(locked.limits):
            raise CannotExecute(
                f"This job needs more than “{locked.name}” holds in total: "
                + "; ".join(wanted.shortfalls(locked.limits))
                + f". Reserve a larger Gear, or ask for less than the "
                f"{op.family.label} default.",
                services.TOO_BIG_FOR_GEAR)

        free = services.gear_available(locked)
        if not wanted.fits_in(free):
            raise CannotExecute(
                f"“{locked.name}” is already running as much as it holds: "
                + "; ".join(wanted.shortfalls(free))
                + ". Wait for a job to finish, or reserve a bigger Gear.",
                services.GEAR_FULL)

        execution = Execution.objects.create(
            lease=locked, operation=op.name, family=op.family.key,
            cpu_millicores=wanted.cpu_millicores, ram_mb=wanted.ram_mb,
            scratch_mb=wanted.scratch_mb, pids=wanted.pids,
            timeout_seconds=clean_timeout,
            subject_label=subject_label, subject_id=str(subject_id or ""),
            requested_by=requested_by if getattr(requested_by, "pk", None) else None,
        )

    if execution.requested_by_id:
        from toto.quota import record_usage

        from .models import AnastasiaUsageEvent
        # Keyed on the execution uuid: submit is retried by callers on a
        # transient manager failure, and a retry must not spend the allowance
        # twice for the same job.
        record_usage(AnastasiaUsageEvent, "anastasia.execution", 1,
                     execution.requested_by,
                     idempotency_key=f"anastasia.execution:{execution.uuid}",
                     source_type="anastasia.Execution",
                     source_id=str(execution.uuid),
                     source_label=execution.operation)

    backend = get_backend()
    try:
        result = backend.start_execution(execution, params=clean_params,
                                         payload=payload)
    except RuntimeUnavailable as exc:
        fail(execution, str(exc), code=services.RUNTIME_UNAVAILABLE)
        raise CannotExecute(str(exc), services.RUNTIME_UNAVAILABLE) from exc
    except Exception as exc:  # noqa: BLE001 — the row is the error channel
        log.exception("anastasia: could not start execution %s", execution.uuid)
        fail(execution, f"The runner could not be started: {exc}")
        raise CannotExecute(
            "The compute manager could not start this job. It has been closed; "
            "try again.") from exc

    execution.status = choices.RUNNING
    execution.started_at = timezone.now()
    execution.save(update_fields=["status", "started_at"])
    services.record(lease=lease, kind=CapsuleEvent.EXECUTE,
                    actor=requested_by, operation=op.name,
                    execution=str(execution.uuid))
    # The TIER comes off the Gear's runtime row, which recorded what the
    # executor reported at mount time — not off a setting. An audit line
    # saying "kata" because the config asked for it would be the one lie this
    # trail exists to prevent.
    _audit("anastasia.job.start", lease=lease, execution=execution,
           actor=requested_by, tier=_tier_of(lease))
    return execution


def _tier_of(lease) -> str:
    """Which isolation the Gear was mounted under, or "" if it is unknown.

    Read from the runtime row rather than from settings, for the reason the
    column exists at all: a setting is what somebody asked for and this is
    what answered.
    """
    runtime = getattr(lease, "runtime", None)
    return getattr(runtime, "tier", "") or ""


def _not_accepting_sentence(lease, state) -> str:
    if state == choices.UNMOUNTED:
        return (f"“{lease.name}” is not mounted. Mount it on the Compute Gears "
                "page and try again.")
    if state == choices.DEGRADED:
        return (f"“{lease.name}” is degraded — something in it ran out of "
                "memory or the manager has stopped answering. Unmount and "
                "mount it again.")
    if state == choices.DEAD:
        return (f"“{lease.name}” is dead: its runtime is gone. Your reservation "
                "is intact — mount it again to bring it back.")
    return f"“{lease.name}” is not accepting work right now."


def finish(execution: Execution, *, exit_code: int = 0, usage: dict | None = None
           ) -> Execution:
    """Close a run that completed. Idempotent — a redelivered result must not
    reopen a row the reconciler already closed."""
    if execution.is_finished:
        return execution
    execution.status = (choices.SUCCESS if exit_code == 0 else choices.FAILED)
    execution.exit_code = exit_code
    execution.usage = usage or {}
    execution.finished_at = timezone.now()
    if exit_code != 0 and not execution.error:
        execution.error = f"The runner exited with status {exit_code}."
    execution.save(update_fields=["status", "exit_code", "usage",
                                  "finished_at", "error"])
    # The OUTCOME, on the same trail as the start. A trail that recorded only
    # what began cannot answer "what happened to it", which is the question
    # anybody actually reads it to answer.
    _audit("anastasia.job.finish", lease=execution.lease, execution=execution,
           actor=execution.requested_by, success=(exit_code == 0),
           exit_code=exit_code, outcome=execution.status,
           # `usage` is the runner's own numbers (cpu seconds, peak memory) —
           # counts, never content.
           usage=usage or {})
    return execution


def fail(execution: Execution, message: str, *, code: str = "",
         status: str = choices.FAILED) -> Execution:
    """Close a run that will never finish. Idempotent; also the sweeper's closer."""
    if execution.is_finished:
        return execution
    execution.status = status
    execution.error = (message or "This job was closed without finishing.")[:500]
    execution.finished_at = timezone.now()
    execution.save(update_fields=["status", "error", "finished_at"])
    if code:
        log.info("anastasia: execution %s closed (%s)", execution.uuid, code)
    _audit("anastasia.job.fail", lease=execution.lease, execution=execution,
           actor=execution.requested_by, success=False,
           outcome=execution.status, refusal_code=code,
           # The sentence a user was shown, so an operator reading the trail
           # sees what the person saw rather than having to reconstruct it.
           error=execution.error)
    return execution


def kill(execution: Execution, *, reason: str = "") -> Execution:
    """Stop a running job on purpose."""
    if execution.is_finished:
        return execution
    killed_cleanly = True
    try:
        get_backend().kill_execution(execution)
    except Exception:  # noqa: BLE001 — the row closes either way
        killed_cleanly = False
        log.exception("anastasia: backend kill failed for %s", execution.uuid)
    # BEFORE `fail` closes the row, so the trail records that somebody stopped
    # this rather than only that it ended. `killed_cleanly` is the part worth
    # keeping: a row closed while the runner may still be alive is exactly what
    # reconciliation has to clean up, and the trail should say so.
    _audit("anastasia.job.kill", lease=execution.lease, execution=execution,
           actor=execution.requested_by, success=killed_cleanly,
           reason=reason, runner_destroyed=killed_cleanly)
    return fail(execution, reason or "This job was stopped.",
                status=choices.KILLED)


def close_stuck(execution_pk) -> None:
    """The dotted-path closer ``sweeps.py`` registers.

    Takes a pk because the sweeper holds nothing else — the shape
    ``toto.quota.sweeps`` requires.
    """
    execution = Execution.objects.filter(pk=execution_pk).first()
    if execution is None:
        return
    fail(execution,
         "This job outlived its Gear's ceiling and was closed by the sweeper.",
         status=choices.LOST)
