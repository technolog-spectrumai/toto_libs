"""The capsule API: everything the desk does, for a client with no session.

The interface is moving to a separate desktop app, so this is the contract that
replaces it. Three rules shape it.

**One vocabulary, not two.** Every view here calls the same `services` and
`execute` functions the desk calls, and returns the same dicts —
`capsule_report`, `pool_report` — that the desk already renders. A second
serialisation layer would be a second place for the truth to drift, and the
first thing to drift would be `isolates_kernel`.

**Refusals keep their shape.** `{"error": <sentence>, "code": <machine>}` with
409, exactly as `views._refusal` produces for the desk. The client branches on
the code and shows the sentence; inventing a second refusal format here would
mean every caller handling both.

**A token is its owner.** There are no per-token scopes. A token reaches
exactly what the person reaches, enforced by the same owner-filtered queries
the desk uses — which 404 somebody else's capsule rather than 403ing it, so the
API does not confirm that a capsule it will not show you exists.
"""

from __future__ import annotations

import functools
import json

from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import choices, execute, jobs, runtime, services
from .limits import Limits, LimitsError
from .models import ComputeLease, Execution
from .tokens import CapsuleToken

#: The version in the path. When a shape must break, v2 appears beside v1 and
#: v1 is left alone — a client in another repository cannot be redeployed in
#: step with this one.
VERSION = "v1"


def _error(message: str, *, code: str = "", status: int = 400):
    return JsonResponse({"error": message, "code": code}, status=status)


def token_required(view):
    """Authenticate a bearer token and hand the view its owner.

    NO SESSION, NO CSRF, ON PURPOSE. A desktop client has neither, and
    `csrf_exempt` here is safe precisely because there is no cookie to ride:
    CSRF protects against a browser attaching ambient credentials to a
    cross-site request, and a bearer token is not ambient.
    """
    @csrf_exempt
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return _error(
                "This endpoint needs a capsule API token. Send it as "
                "`Authorization: Bearer <token>`.",
                code="no_token", status=401)
        token = CapsuleToken.authenticate(header[len("Bearer "):].strip())
        if token is None:
            return _error(
                "That token is not valid, or it has been revoked or expired.",
                code="bad_token", status=401)
        token.touch()
        request.capsule_token = token
        return view(request, token.owner, *args, **kwargs)
    return wrapper


def _own_lease(owner, uuid) -> ComputeLease:
    """Somebody else's capsule is a 404, never a 403.

    The same rule the desk uses: a 403 would confirm the capsule exists, which
    is information the owner did not agree to share.
    """
    lease = ComputeLease.objects.open().filter(owner=owner, uuid=uuid).first()
    if lease is None:
        raise Http404("no such capsule")
    return lease


def _refusal(exc):
    message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
    return _error(message, code=getattr(exc, "refusal_code", ""), status=409)


def _body(request) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        return {}


# --------------------------------------------------------------------------- #
# Pool and capsules                                                            #
# --------------------------------------------------------------------------- #

@require_GET
@token_required
def pool(request, owner):
    return JsonResponse(services.pool_report())


@require_GET
@token_required
def capsule_list(request, owner):
    leases = ComputeLease.objects.open().filter(owner=owner).order_by("created_at")
    return JsonResponse({"capsules": [services.capsule_report(l) for l in leases]})


@require_POST
@token_required
def capsule_create(request, owner):
    payload = _body(request)
    try:
        limits = Limits.from_mapping(payload.get("limits"))
    except (LimitsError, ValueError) as exc:
        return _error(str(exc), code="bad_limits")
    try:
        lease = services.reserve(
            owner=owner, name=payload.get("name") or "",
            limits=limits, days=payload.get("days"),
            permanent_home=bool(payload.get("permanent_home")))
    except ValidationError as exc:
        return _refusal(exc)
    return JsonResponse(services.capsule_report(lease), status=201)


@require_GET
@token_required
def capsule_detail(request, owner, uuid):
    lease = _own_lease(owner, uuid)
    services.refresh_runtime(lease)
    return JsonResponse(services.capsule_report(lease))


@require_POST
@token_required
def capsule_action(request, owner, uuid, action):
    lease = _own_lease(owner, uuid)
    verbs = {"mount": services.mount, "unmount": services.unmount,
             "release": services.release}
    verb = verbs.get(action)
    if verb is None:
        return _error(f"no such action {action!r}", code="bad_action",
                      status=404)
    try:
        verb(lease=lease, actor=owner)
    except ValidationError as exc:
        return _refusal(exc)
    lease.refresh_from_db()
    return JsonResponse(services.capsule_report(lease))


@require_GET
@token_required
def capsule_storage(request, owner, uuid):
    """How much disk this capsule holds. COUNTS ONLY.

    Sizes and counts, never a filename and never any content — the boundary is
    enforced in `executor/storage.py`, which has no parameter that could widen
    it into a listing. An operator may know a capsule is using 4 GB; what is in
    it remains the owner's business.

    A separate endpoint rather than a field on the capsule report, because it
    costs a filesystem walk and the report is polled.
    """
    lease = _own_lease(owner, uuid)
    backend = runtime.get_backend()
    reading = getattr(backend, "storage", None)
    if reading is None:
        return _error("this deployment's runtime cannot report storage",
                      code="unsupported", status=501)
    return JsonResponse(reading(lease) or {"complete": False})


# --------------------------------------------------------------------------- #
# Jobs                                                                         #
# --------------------------------------------------------------------------- #

def _job_json(execution) -> dict:
    return {
        "uuid": str(execution.uuid),
        "capsule": str(execution.lease.uuid),
        "operation": execution.operation,
        "family": execution.family,
        "status": execution.status,
        # `choices.FINISHED` rather than a list written here: the client polls
        # until this is true, and a set that drifts from the model's own would
        # make it poll a dead job for ever.
        "finished": execution.status in choices.FINISHED,
        "exit_code": execution.exit_code,
        "error": execution.error,
        "created_at": execution.created_at,
        "finished_at": execution.finished_at,
    }


@require_POST
@token_required
def job_create(request, owner, uuid):
    lease = _own_lease(owner, uuid)
    payload = _body(request)
    try:
        execution = execute.submit(
            lease=lease, operation=payload.get("operation") or "",
            params=payload.get("params") or {},
            requested_by=owner)
    except ValidationError as exc:
        return _refusal(exc)
    return JsonResponse(_job_json(execution), status=201)


def _own_execution(owner, uuid) -> Execution:
    execution = Execution.objects.filter(
        uuid=uuid, lease__owner=owner).select_related("lease").first()
    if execution is None:
        raise Http404("no such job")
    return execution


@require_GET
@token_required
def job_detail(request, owner, uuid):
    return JsonResponse(_job_json(_own_execution(owner, uuid)))


@require_GET
@token_required
def job_output(request, owner, uuid):
    """The files a finished job produced, as ``{name: base64}``.

    Base64 rather than a tar download, because the client is a desktop app
    assembling its own view and not a browser saving a file — and because a
    JSON body keeps this endpoint the same shape as every other one here.

    REFUSED WHILE THE JOB IS STILL RUNNING, rather than returning what happens
    to be on disk so far. A partial output that looks complete is the kind of
    answer a client writes to a file and a person then trusts. The client polls
    `finished` and asks once.
    """
    import base64

    execution = _own_execution(owner, uuid)
    if execution.status not in choices.FINISHED:
        return _error(
            "this job has not finished, so its output is not complete yet",
            code="still_running", status=409)
    backend = runtime.get_backend()
    try:
        blob = backend.collect(execution)
    except Exception as exc:                     # noqa: BLE001
        # The runtime being unreachable is not the caller's mistake, and a 500
        # would send a client into a retry loop against a machine that is down.
        return _error(f"the runtime could not return this job's output ({exc})",
                      code="runtime_unavailable", status=503)
    files = jobs.files_from(blob)
    return JsonResponse({
        "uuid": str(execution.uuid),
        "files": {name: base64.b64encode(body).decode("ascii")
                  for name, body in sorted(files.items())},
    })
