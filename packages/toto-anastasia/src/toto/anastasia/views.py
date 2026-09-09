"""The Compute Gears desk: reserve, mount, watch, unmount.

One page and a handful of POST targets. The shape follows the platform's
convention — a page that renders derived state, JSON endpoints the page polls,
and every write re-checking permission server-side so a hand-posted form is
still refused.

**The shared secret never leaves this process.** The browser talks to Zenobia;
Zenobia signs and talks to the manager. That is the whole reason
``executor_backend`` exists on this side of the wire rather than the page
calling the manager directly.

Ownership is simple and strict: a Gear belongs to the user who reserved it.
Staff can SEE the pool (they have to, to run the machine) but a Gear is not a
shared resource and there is no borrowing.
"""

from __future__ import annotations

import json
import logging

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from . import choices, conf, families, services
from .limits import Limits, LimitsError
from .models import ComputeLease, Execution, GearRuntime
from .runtime import RuntimeUnavailable, get_backend

log = logging.getLogger("toto.anastasia.views")


def _own_lease(request, uuid) -> ComputeLease:
    """A Gear the requesting user actually holds.

    404 rather than 403 for somebody else's Gear: whether a given uuid exists
    is not information a stranger needs, and a 403 answers that question.
    """
    # Filtered by owner in the QUERY, not checked after fetching: that is what
    # makes somebody else's Gear indistinguishable from one that does not
    # exist. Raising PermissionDenied here instead would answer "this uuid is
    # real" to anyone who asked, which is the question the docstring above says
    # not to answer.
    return get_object_or_404(ComputeLease, uuid=uuid, owner=request.user)


def _refusal(request, exc, lease=None):
    """One refusal, rendered the way the caller asked for it."""
    message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
    code = getattr(exc, "refusal_code", "")
    if request.headers.get("Accept", "").startswith("application/json"):
        return JsonResponse({"error": message, "code": code}, status=409)
    from django.contrib import messages as django_messages

    django_messages.error(request, message)
    return redirect("anastasia:index")


@login_required
def index(request):
    """Everything a person needs to decide whether to reserve.

    The pool report is shown to everyone, not just staff: "reserve" is a choice
    made against a number, and hiding the number turns a refusal into a
    mystery.
    """
    leases = list(ComputeLease.objects.open()
                  .filter(owner=request.user).select_related("runtime"))
    gears = [services.gear_report(lease) for lease in leases]
    report = services.pool_report()

    # Only MOUNTED gears are polled. An unmounted one has nothing to report,
    # and asking would wake the manager once every five seconds for nothing.
    poll_urls = {
        gear["uuid"]: reverse("anastasia:status", args=[gear["uuid"]])
        for gear in gears if gear["state"] in choices.MOUNTED
    }

    # PageProcessor, like every other page on the platform. It is not
    # decoration: oya/base.html builds the Tailwind palette from
    # `theme.theme.colors`, and with no theme in the context that expression
    # renders `{}` — so every custom colour class silently stops existing.
    # This page shipped without it and the result was a desk that ignored the
    # dark-mode toggle, drew its cards with default hairline borders, and
    # rendered the Reserve button as white text on a background that was never
    # applied: present, clickable, invisible. base.html warns about exactly
    # this in the comment above that `colors:` line.
    return render(request, "anastasia/index.html", PageProcessor().decorate({
        "pool": report,
        "pool_rows": _pool_rows(report),
        "gears": gears,
        "runtime_configured": bool(conf.executor_socket()),
        "max_gears": conf.max_gears_per_user(),
        "lease_days": conf.lease_days(),
        "held": len(leases),
        "poll_urls_json": json.dumps(poll_urls),
        "stale_seconds": conf.sample_stale_seconds(),
    }, request))


#: (field, label, unit) for the pool strip. A list rather than four template
#: blocks so the dimensions cannot drift apart visually.
_POOL_FIELDS = (
    ("cpu_millicores", _("CPU"), "mCPU"),
    ("ram_mb", _("RAM"), "MB"),
    ("scratch_mb", _("Scratch"), "MB"),
    ("pids", _("Processes"), ""),
)


def _pool_rows(report: dict) -> list:
    return [
        {
            "label": label,
            "available": f"{report['available'][field]}{(' ' + unit) if unit else ''}",
            "total": f"{report['total'][field]}{(' ' + unit) if unit else ''}",
        }
        for field, label, unit in _POOL_FIELDS
    ]


@login_required
@require_POST
def reserve(request):
    try:
        limits = Limits.from_mapping({
            "cpu_millicores": int(request.POST.get("cpu_millicores") or 0),
            "ram_mb": int(request.POST.get("ram_mb") or 0),
            "scratch_mb": int(request.POST.get("scratch_mb") or 0),
            "pids": int(request.POST.get("pids") or 0),
        })
    except (TypeError, ValueError, LimitsError) as exc:
        return _refusal(request, ValidationError(
            _("Those capacity numbers are not whole numbers: %(detail)s")
            % {"detail": exc}))

    try:
        lease = services.reserve(owner=request.user,
                                 name=request.POST.get("name", ""),
                                 limits=limits, actor=request.user)
    except ValidationError as exc:
        return _refusal(request, exc)

    from django.contrib import messages as django_messages

    django_messages.success(request, _(
        "“%(name)s” is reserved. Mount it when you want to use it — the "
        "capacity is yours either way.") % {"name": lease.name})
    return redirect("anastasia:index")


@login_required
@require_POST
def mount(request, uuid):
    lease = _own_lease(request, uuid)
    try:
        services.mount(lease=lease, actor=request.user)
    except ValidationError as exc:
        return _refusal(request, exc, lease)
    return redirect("anastasia:index")


@login_required
@require_POST
def unmount(request, uuid):
    lease = _own_lease(request, uuid)
    services.unmount(lease=lease, actor=request.user, reason="unmounted by owner")
    return redirect("anastasia:index")


@login_required
@require_POST
def release(request, uuid):
    """Give the capacity back for good. Unmounts on the way out."""
    lease = _own_lease(request, uuid)
    services.release(lease=lease, reason="released by owner", actor=request.user)
    from django.contrib import messages as django_messages

    django_messages.success(request, _(
        "“%(name)s” is released and its capacity is back in the pool.")
        % {"name": lease.name})
    return redirect("anastasia:index")


@login_required
def status(request, uuid):
    """What the cards poll.

    Asks the manager for a live sample and folds it onto the row, so the page
    shows what the Gear IS doing rather than what it was doing when somebody
    last loaded it. A manager that does not answer leaves the previous sample
    in place with its age visible — see ``services.derive_state``, which turns
    a stale sample into DEGRADED rather than into a confident lie.
    """
    lease = _own_lease(request, uuid)
    services.refresh_runtime(lease)
    return JsonResponse(services.gear_report(lease))


@login_required
def pool(request):
    return JsonResponse(services.pool_report())


# --------------------------------------------------------------------------- #
# The operator's page                                                          #
# --------------------------------------------------------------------------- #
# Separate from the Gear desk, and staff-only, because the questions differ.
# A user asks "can I run my job"; an operator asks "what is this machine doing,
# and how do I stop it". The second needs the whole pool, every live job across
# every owner, and the two switches — none of which belongs on a page a user
# sees.


def _staff_only(user) -> bool:
    return bool(user.is_active and (user.is_staff or user.is_superuser))


@login_required
def operator(request):
    """What this machine is doing, and the switches to stop it.

    Read-only except for two POSTs, and both are deliberate acts with a
    confirmation: draining is reversible and destroys nothing, stopping throws
    running work away. They are separate buttons with separate words for the
    same reason they are separate verbs on the wire.
    """
    if not _staff_only(request.user):
        raise Http404

    backend = get_backend()
    admission = {"state": "unknown", "reason": "", "since": None}
    health: dict = {}
    unreachable = ""
    try:
        # `getattr` because the null backend has no admission control — there
        # is nothing to drain when nothing runs — and a staff page must render
        # on a host with no executor rather than 500.
        reader = getattr(backend, "admission", None)
        if reader is not None:
            admission = (reader() or {}).get("admission", admission)
        describe = getattr(backend, "health", None)
        if describe is not None:
            health = describe() or {}
    except RuntimeUnavailable as exc:
        unreachable = str(exc)

    live = (Execution.objects.live()
            .select_related("lease", "requested_by")
            .order_by("-created_at")[:100])
    return render(request, "anastasia/operator.html", PageProcessor().decorate({
        "pool": services.pool_report(),
        "admission": admission,
        "health": health,
        "unreachable": unreachable,
        "live": live,
        "mounted": (GearRuntime.objects.filter(state=choices.READY)
                    .select_related("lease", "lease__owner")
                    .order_by("-mounted_at")[:100]),
    }, request))


@login_required
@require_POST
def operator_control(request, action):
    """Drain, resume or stop. Staff only, POST only, and never silent.

    Every one of these is recorded on the audit trail before it is attempted,
    with the operator's name on it: an emergency stop destroys other people's
    work, and the trail is where that decision has to be answerable.
    """
    if not _staff_only(request.user):
        raise Http404

    verbs = {"drain": "drain", "resume": "resume", "stop": "emergency_stop"}
    if action not in verbs:
        raise Http404

    reason = (request.POST.get("reason") or "")[:400]
    backend = get_backend()

    # AUDITED BEFORE ANYTHING ELSE, including before checking whether there is
    # a runtime to control. An operator reaching for the emergency stop on a
    # host whose executor is unreachable is exactly the moment worth having a
    # record of: what they tried, when, and that it did not take. Recording
    # only successful attempts would leave the interesting half out.
    _audit_control(action, request.user, reason)

    call = getattr(backend, verbs[action], None)
    if call is None:
        messages.error(request, _(
            "This deployment has no compute runtime to control."))
        return redirect("anastasia:operator")
    try:
        result = call(reason) or {}
    except RuntimeUnavailable as exc:
        messages.error(request, str(exc))
        return redirect("anastasia:operator")

    if action == "stop":
        messages.warning(request, _(
            "Compute is stopped. %(n)s running job(s) were destroyed.")
            % {"n": result.get("runners_destroyed", 0)})
    elif action == "drain":
        messages.success(request, _(
            "This machine is draining: running work will finish, and no new "
            "work will start."))
    else:
        messages.success(request, _("This machine is taking work again."))
    return redirect("anastasia:operator")


def _audit_control(action: str, actor, reason: str) -> None:
    """BEFORE the call, not after.

    An emergency stop that killed fifty jobs and then failed to write its own
    record would leave the destruction unexplained. Recording the intent first
    means the trail is never behind the machine.
    """
    try:
        from toto.audit import record

        record(f"anastasia.control.{action}", app_label="anastasia",
               description=f"compute {action}", actor_user=actor,
               success=True, metadata={"reason": reason} if reason else {})
    except Exception:  # noqa: BLE001 — evidence, never a dependency
        log.exception("anastasia: could not audit a control action")
