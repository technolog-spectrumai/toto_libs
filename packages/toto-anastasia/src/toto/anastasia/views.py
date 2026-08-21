"""The Compute Gears desk: reserve, mount, watch, unmount.

One page and a handful of POST targets. The shape follows the platform's
convention — a page that renders derived state, JSON endpoints the page polls,
and every write re-checking permission server-side so a hand-posted form is
still refused.

**The shared secret never leaves this process.** The browser talks to Zenobia;
Zenobia signs and talks to the manager. That is the whole reason
``manager_backend`` exists on this side of the wire rather than the page
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
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from . import choices, conf, families, services
from .limits import Limits, LimitsError
from .models import ComputeLease

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

    return render(request, "anastasia/index.html", {
        "pool": report,
        "pool_rows": _pool_rows(report),
        "gears": gears,
        "warmable": sorted(f.key for f in families.FAMILIES.values() if f.warmable),
        "manager_configured": bool(conf.manager_url()),
        "max_gears": conf.max_gears_per_user(),
        "lease_days": conf.lease_days(),
        "held": len(leases),
        "poll_urls_json": json.dumps(poll_urls),
        "stale_seconds": conf.sample_stale_seconds(),
    })


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
@require_POST
def set_warm(request, uuid):
    """How many runtimes to keep alive inside this Gear."""
    lease = _own_lease(request, uuid)
    policy = {}
    for key in families.FAMILIES:
        raw = request.POST.get(f"warm_{key}")
        if raw:
            try:
                policy[key] = int(raw)
            except ValueError:
                return _refusal(request, ValidationError(
                    _("The warm count for %(family)s must be a whole number.")
                    % {"family": key}))
    try:
        services.set_warm_policy(lease=lease, policy=policy, actor=request.user)
    except ValidationError as exc:
        return _refusal(request, exc, lease)
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
