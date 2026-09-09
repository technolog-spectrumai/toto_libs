"""Everything that changes a booking, in one place.

Keyword-only arguments, ``ValidationError`` with a sentence a person can act
on, and the transaction boundary drawn where it belongs — the conventions the
rest of the suite uses.

This module owns the ARITHMETIC (does it fit, what is left, who holds what) and
the DURABLE state. It does not own containers: mounting delegates to a
:mod:`~toto.anastasia.runtime` backend, so every function here is testable with
no Docker and no manager.

One rule runs through all of it: **a reservation is deducted while idle.** The
pool arithmetic never asks whether a Gear is mounted or busy, only whether its
lease is open. That is what makes a reservation a reservation.
"""

from __future__ import annotations

import datetime
import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from . import choices, conf
from .limits import Limits, LimitsError, validate_reservation
from .models import ComputeLease, Execution, GearEvent, GearRuntime, PoolGuard
from .runtime import RuntimeUnavailable, get_backend

log = logging.getLogger("toto.anastasia.services")

#: Refusal codes. They travel in the history and in JSON payloads, so they are
#: part of the interface — a page can branch on them, a person can grep.
POOL_UNCONFIGURED = "pool_unconfigured"
POOL_EXHAUSTED = "pool_exhausted"
TOO_MANY_GEARS = "too_many_gears"
LEASE_CLOSED = "lease_closed"
NOT_MOUNTED = "not_mounted"
GEAR_FULL = "gear_full"
TOO_BIG_FOR_GEAR = "too_big_for_gear"
RUNTIME_UNAVAILABLE = "runtime_unavailable"
#: Not a capacity refusal at all — the daily submission allowance is spent.
#: Distinct from GEAR_FULL, which is about this instant and clears by itself.
QUOTA_EXCEEDED = "quota_exceeded"
#: Also not capacity: a recurring fee went unpaid and the account has stopped
#: accepting new metered work. Kept distinct from QUOTA_EXCEEDED because
#: toto.quota keeps the exceptions distinct for the same reason — "over a rate
#: limit, try later" and "a levy went unpaid, top up" are different problems
#: with different fixes, and one message for both tells half the users the
#: wrong thing. It is 402 to quota's 429.
IN_ARREARS = "in_arrears"


class CapacityError(ValidationError):
    """A refusal that carries its code as well as its sentence."""

    def __init__(self, message, code: str = ""):
        super().__init__(message)
        self.refusal_code = code


# --------------------------------------------------------------------------- #
# Reading the pool                                                             #
# --------------------------------------------------------------------------- #

def booked(now=None) -> Limits:
    """The sum of every OPEN lease — mounted or not, busy or not."""
    row = ComputeLease.objects.open(now).aggregate(
        cpu=Sum("cpu_millicores"), ram=Sum("ram_mb"),
        scratch=Sum("scratch_mb"), pids=Sum("pids"))
    return Limits(row["cpu"] or 0, row["ram"] or 0, row["scratch"] or 0,
                  row["pids"] or 0)


def available(now=None) -> Limits:
    """What is left to reserve. Clamped at zero — see ``Limits.__sub__``."""
    return conf.pool_limits() - booked(now)


def pool_report(now=None) -> dict:
    """What the Gear page draws above the reserve form."""
    total, used = conf.pool_limits(), booked(now)
    free = total - used
    return {
        "configured": not total.is_zero,
        "total": total.as_dict(),
        "booked": used.as_dict(),
        "available": free.as_dict(),
        "gears_open": ComputeLease.objects.open(now).count(),
    }


def gear_available(lease, now=None) -> Limits:
    """What is left INSIDE one Gear.

    The same arithmetic one level down: a Gear is a little pool, and its live
    executions book against it exactly as its lease books against the host. Two
    levels, one algebra — which is why a Gear can host several concurrent jobs
    without any of them being able to exceed what the user reserved.
    """
    row = lease.executions.live().aggregate(
        cpu=Sum("cpu_millicores"), ram=Sum("ram_mb"),
        scratch=Sum("scratch_mb"), pids=Sum("pids"))
    running = Limits(row["cpu"] or 0, row["ram"] or 0, row["scratch"] or 0,
                     row["pids"] or 0)
    return lease.limits - running


# --------------------------------------------------------------------------- #
# History                                                                      #
# --------------------------------------------------------------------------- #

def record(*, lease, kind, accepted=True, code="", actor=None,
           from_state="", to_state="", **detail) -> GearEvent:
    return GearEvent.objects.create(
        lease=lease, kind=kind, accepted=accepted, refusal_code=code,
        actor=actor if getattr(actor, "pk", None) else None,
        from_state=from_state, to_state=to_state, detail=detail or {})


# --------------------------------------------------------------------------- #
# Reserving                                                                    #
# --------------------------------------------------------------------------- #

def reserve(*, owner, name: str, limits: Limits, days: int | None = None,
            actor=None, permanent_home: bool = False) -> ComputeLease:
    """Book capacity for a user, or refuse and say what is short.

    The whole function runs inside one transaction that begins by locking the
    pool guard, because "does it fit" is a SUM and a sum has no row to lock —
    see :class:`~toto.anastasia.models.PoolGuard`.
    """
    name = (name or "").strip()
    if not name:
        raise CapacityError(
            "A Gear needs a name — it is how you will pick it when you start "
            "heavy work.")
    if not conf.pool_is_configured():
        raise CapacityError(
            "This deployment has no compute pool, so nothing can be reserved. "
            "An administrator sets ANASTASIA_POOL.", POOL_UNCONFIGURED)

    try:
        validate_reservation(limits)
    except LimitsError as exc:
        raise CapacityError(str(exc)) from exc

    days = conf.lease_days() if days is None else max(1, min(int(days),
                                                             conf.MAX_LEASE_DAYS))
    expires_at = timezone.now() + datetime.timedelta(days=days)

    with transaction.atomic():
        _lock_pool()

        held = ComputeLease.objects.open().filter(owner=owner).count()
        if held >= conf.max_gears_per_user():
            raise CapacityError(
                f"You already hold {held} Compute Gears, which is the limit "
                f"here. Release one before reserving another.", TOO_MANY_GEARS)

        free = available()
        if not limits.fits_in(free):
            raise CapacityError(
                "The pool does not have room for that Gear: "
                + "; ".join(limits.shortfalls(free)) + ".",
                POOL_EXHAUSTED)

        if ComputeLease.objects.open().filter(owner=owner, name=name).exists():
            raise CapacityError(
                f"You already have a live Gear called “{name}”. Pick another "
                "name, or release that one first.")

        lease = ComputeLease.objects.create(
            owner=owner, name=name, expires_at=expires_at,
            cpu_millicores=limits.cpu_millicores, ram_mb=limits.ram_mb,
            scratch_mb=limits.scratch_mb, pids=limits.pids,
            permanent_home=bool(permanent_home))
        GearRuntime.objects.create(lease=lease)
        record(lease=lease, kind=GearEvent.RESERVE, actor=actor or owner,
               to_state=choices.UNMOUNTED, **limits.as_dict())
        return lease


def _lock_pool() -> None:
    """Serialise admission on both backends. See PoolGuard's docstring."""
    guard, _ = PoolGuard.objects.get_or_create(pk=1)
    # select_for_update locks on postgres; the update takes sqlite's write lock.
    PoolGuard.objects.select_for_update().filter(pk=1).first()
    PoolGuard.objects.filter(pk=1).update(touched=guard.touched + 1)


def release(*, lease: ComputeLease, reason: str = "", actor=None) -> ComputeLease:
    """Give the capacity back. Idempotent — the first release is the true one.

    Unmounts first: capacity that is still running something has not been
    returned to the pool, whatever the row says.
    """
    if lease.released_at is not None:
        return lease

    try:
        unmount(lease=lease, actor=actor, reason="lease released")
    except Exception:  # noqa: BLE001 — a stuck runtime must not strand a lease
        log.exception("anastasia: unmount failed while releasing %s", lease.uuid)

    now = timezone.now()
    updated = ComputeLease.objects.filter(
        pk=lease.pk, released_at__isnull=True).update(
            released_at=now, release_reason=(reason or "")[:200])
    if updated:
        lease.released_at = now
        lease.release_reason = (reason or "")[:200]
        record(lease=lease, kind=GearEvent.RELEASE, actor=actor,
               to_state=choices.UNMOUNTED, reason=reason)
    return lease


def expire_due(now=None) -> int:
    """Release every lease whose reservation has run out. Returns how many."""
    now = now or timezone.now()
    count = 0
    for lease in list(ComputeLease.objects.due_to_expire(now)):
        release(lease=lease, reason=f"reservation expired {lease.expires_at:%Y-%m-%d %H:%M}")
        record(lease=lease, kind=GearEvent.EXPIRE, to_state=choices.UNMOUNTED)
        count += 1
    return count


# --------------------------------------------------------------------------- #
# Mounting                                                                     #
# --------------------------------------------------------------------------- #

def runtime_for(lease: ComputeLease) -> GearRuntime:
    runtime, _ = GearRuntime.objects.get_or_create(lease=lease)
    return runtime


def mount(*, lease: ComputeLease, actor=None) -> GearRuntime:
    """Bring the Gear up. Refuses on a closed lease; idempotent when mounted."""
    if not lease.is_open():
        raise CapacityError(
            "That Gear's reservation has ended, so it cannot be mounted. "
            "Reserve a new one.", LEASE_CLOSED)

    runtime = runtime_for(lease)
    if runtime.is_mounted:
        return runtime

    backend = get_backend()
    try:
        result = backend.mount(lease)
    except RuntimeUnavailable as exc:
        record(lease=lease, kind=GearEvent.MOUNT, accepted=False,
               code=RUNTIME_UNAVAILABLE, actor=actor,
               from_state=runtime.state, reason=str(exc))
        raise CapacityError(str(exc), RUNTIME_UNAVAILABLE) from exc

    now = timezone.now()
    previous = runtime.state
    runtime.state = choices.READY
    runtime.state_at = now
    runtime.mounted_at = now
    runtime.unmounted_at = None
    runtime.detail = ""
    runtime.manager_generation = str(result.get("manager_generation", ""))[:64]
    runtime.save(update_fields=[
        "state", "state_at", "mounted_at", "unmounted_at", "detail",
        "manager_generation"])
    record(lease=lease, kind=GearEvent.MOUNT, actor=actor,
           from_state=previous, to_state=choices.READY)
    return runtime


def unmount(*, lease: ComputeLease, actor=None, reason: str = "") -> GearRuntime:
    """Take the Gear down. Idempotent, and never raises on an absent runtime.

    Live executions are marked KILLED here rather than left RUNNING: their
    containers are gone with the slice, and a row that still claims to be
    running is the one outcome a caller polling it cannot recover from.
    """
    runtime = runtime_for(lease)
    backend = get_backend()
    try:
        result = backend.unmount(lease)
    except Exception as exc:  # noqa: BLE001 — teardown must not be blockable
        log.exception("anastasia: backend unmount failed for %s", lease.uuid)
        result = {"error": str(exc)}

    killed = lease.executions.live().update(
        status=choices.KILLED, finished_at=timezone.now(),
        error="The Gear was unmounted while this was running.")

    previous = runtime.state
    if previous != choices.UNMOUNTED:
        runtime.state = choices.UNMOUNTED
        runtime.state_at = timezone.now()
        runtime.unmounted_at = timezone.now()
        runtime.detail = ""
        runtime.last_sample = {}
        runtime.sampled_at = None
        runtime.save(update_fields=[
            "state", "state_at", "unmounted_at", "detail", "last_sample",
            "sampled_at"])
        record(lease=lease, kind=GearEvent.UNMOUNT, actor=actor,
               from_state=previous, to_state=choices.UNMOUNTED,
               reason=reason, executions_killed=killed,
               runners_destroyed=result.get("runners_destroyed"))
    return runtime


# --------------------------------------------------------------------------- #
# Deriving what a Gear is doing                                                #
# --------------------------------------------------------------------------- #

def derive_state(runtime: GearRuntime, now=None) -> str:
    """What the Gear is, from what we know. A pure function of the row.

    Called by the status page as well as by the reconciler, so a stack whose
    manager is down still shows the truth rather than the last thing written.
    """
    now = now or timezone.now()
    if not runtime.lease.is_open(now):
        return choices.UNMOUNTED
    if runtime.state == choices.UNMOUNTED:
        return choices.UNMOUNTED
    if runtime.state == choices.DEAD:
        return choices.DEAD

    age = runtime.sample_age_seconds(now)
    if age is not None and age > conf.sample_stale_seconds():
        # The manager has stopped answering about a Gear it claims to hold.
        # Reading that as READY would be the "idle-detector reads a broken
        # probe as zero" bug lifecycle's registry warns about.
        return choices.DEGRADED
    if runtime.last_sample.get("oom_kills"):
        return choices.DEGRADED
    if runtime.lease.executions.live().exists():
        return choices.BUSY
    return choices.READY


def refresh_runtime(lease: ComputeLease) -> GearRuntime:
    """Ask the manager what this Gear is doing, and record the answer.

    Never raises. A manager that does not answer leaves the PREVIOUS sample in
    place with its timestamp untouched, which is what lets ``derive_state``
    notice the silence and report DEGRADED. Overwriting the sample with an
    empty one would erase exactly the evidence that something is wrong, and
    clearing ``sampled_at`` would make a silent manager look like a Gear that
    has simply never been sampled.
    """
    runtime = runtime_for(lease)
    if not runtime.is_mounted:
        return runtime

    try:
        answer = get_backend().status(lease) or {}
    except Exception:  # noqa: BLE001 - a status read must never break a page
        log.exception("anastasia: could not read status for %s", lease.uuid)
        return runtime

    sample = answer.get("sample")
    if not sample:
        return runtime

    fields = ["last_sample", "sampled_at"]
    runtime.last_sample = sample
    runtime.sampled_at = timezone.now()

    # A manager generation that has moved means the process we mounted against
    # is gone. The runtime it created went with it, so this Gear is DEAD until
    # its owner mounts it again — the reservation is untouched either way.
    generation = str(answer.get("manager_generation") or "")
    if (generation and runtime.manager_generation
            and generation != runtime.manager_generation):
        runtime.state = choices.DEAD
        runtime.state_at = timezone.now()
        runtime.detail = (
            "The compute manager restarted, so this Gear's runtime is gone. "
            "Your reservation is intact — mount it again.")
        fields += ["state", "state_at", "detail"]
        record(lease=lease, kind=GearEvent.RECONCILE,
               from_state=choices.READY, to_state=choices.DEAD,
               reason="manager generation changed",
               was=runtime.manager_generation, now=generation)
    elif not answer.get("mounted", True):
        # The manager is the same process but has no record of this Gear —
        # it was torn down out from under us (a reconcile, an operator).
        runtime.state = choices.DEAD
        runtime.state_at = timezone.now()
        runtime.detail = ("The compute manager no longer holds this Gear. "
                          "Mount it again to bring it back.")
        fields += ["state", "state_at", "detail"]

    runtime.save(update_fields=fields)
    return runtime


def gear_report(lease: ComputeLease, now=None) -> dict:
    """One Gear, as the page draws it."""
    now = now or timezone.now()
    runtime = runtime_for(lease)
    state = derive_state(runtime, now)
    free = gear_available(lease, now)
    return {
        "uuid": str(lease.uuid),
        "name": lease.name,
        "state": state,
        "detail": runtime.detail,
        "reserved": lease.limits.as_dict(),
        "free": free.as_dict(),
        "usage": runtime.last_sample,
        "sample_age_seconds": runtime.sample_age_seconds(now),
        "expires_at": lease.expires_at,
        "executions_running": lease.executions.live().count(),
        "accepts_work": state in choices.ACCEPTING,
    }
