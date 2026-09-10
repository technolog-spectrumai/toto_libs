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
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from . import choices, execute, install, jobs, runtime, services
from .limits import Limits, LimitsError
from .models import ComputeLease, Execution, InstallRun
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
def me(request, owner):
    """Who this token is, and which token it is.

    THE FIRST CALL A CLIENT MAKES. It proves the credential works and names it
    in one round trip, which is what a "connect" screen needs — and `/pool`,
    the obvious alternative, answers 200 for a valid token without saying
    whose it is.

    `label` is the string the person typed when they minted it ("Laptop —
    Python editor"). Showing it back is what makes a client's connection
    screen legible: two machines signed into the same account are otherwise
    indistinguishable, and "revoke the one that says laptop" needs the label
    to have travelled.

    NOTHING SECRET IS RETURNED. `hint` is the last six characters, which is
    what the desk shows too — enough to match a row to a config file, not
    enough to reconstruct anything. The selector is deliberately absent even
    though the server stores it in the clear: it is half the credential.
    """
    token = request.capsule_token
    return JsonResponse({
        "username": owner.get_username(),
        "is_staff": bool(owner.is_staff),
        "token": {
            "label": token.label,
            "hint": token.hint,
            "created_at": token.created_at,
            "expires_at": token.expires_at,
        },
    })


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


#: The most a client may stage into one job, before base64 expansion.
#:
#: A ceiling here rather than only in the executor's tar guard, because this
#: endpoint decodes into memory to build the tar: an unbounded body would be a
#: memory amplifier on the WEB tier, which the executor's own limits do not
#: protect. 32 MB is far above a source tree and far below a problem.
MAX_INPUT_BYTES = 32 * 1024 * 1024

#: How many files. A tar of a hundred thousand one-byte members is small on the
#: wire and slow everywhere after it.
MAX_INPUT_FILES = 512


def _decode_inputs(raw):
    """``{name: base64}`` from a client to ``{name: bytes}``, or a refusal.

    Returns ``(files, error_response)`` — exactly one is meaningful.

    THE NAMES ARE NOT TRUSTED HERE and are not the last check either: the
    executor re-validates every member on the way in (`staging.py` resolves
    paths and refuses links and traversal). What this rejects is the shape a
    caller can get wrong by accident, so the refusal names the file rather
    than arriving as a tar error with no context.
    """
    import base64
    import binascii

    if raw in (None, {}):
        return {}, None
    if not isinstance(raw, dict):
        return None, _error("`inputs` must be a mapping of filename to "
                            "base64 content.", code="bad_inputs")
    if len(raw) > MAX_INPUT_FILES:
        return None, _error(
            f"that is {len(raw)} input files; at most {MAX_INPUT_FILES} may be "
            f"staged into one job.", code="too_many_inputs")

    files, total = {}, 0
    for name, encoded in raw.items():
        if not isinstance(name, str) or not name:
            return None, _error("every input needs a filename.",
                                code="bad_inputs")
        # Flat names only. The runner reads from one staged directory and a
        # path here is either a mistake or an attempt; either way the honest
        # answer names the file.
        if name.startswith("/") or ".." in name.split("/"):
            return None, _error(
                f"“{name}” is not a filename this job may stage.",
                code="bad_input_name")
        if not isinstance(encoded, str):
            return None, _error(f"“{name}” must be base64 text.",
                                code="bad_inputs")
        try:
            body = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return None, _error(f"“{name}” is not valid base64.",
                                code="bad_inputs")
        total += len(body)
        if total > MAX_INPUT_BYTES:
            return None, _error(
                f"the staged input is over the "
                f"{MAX_INPUT_BYTES // (1024 * 1024)} MB limit.",
                code="inputs_too_large", status=413)
        files[name] = body
    return files, None


@require_POST
@token_required
def job_create(request, owner, uuid):
    """Submit a job, optionally with the files it should run on.

    **`inputs` is what makes this endpoint usable.** Until 2026-09-10 it
    accepted only `operation` and `params`, so a client could start
    `compile_latex` or `run_python` and had no way to say WHAT to compile or
    run — every family this API can reach needs a staged file. The desk's own
    callers never noticed, because they call `jobs.run` in-process and pass
    `inputs=` there.
    """
    lease = _own_lease(owner, uuid)
    payload = _body(request)
    files, refusal = _decode_inputs(payload.get("inputs"))
    if refusal is not None:
        return refusal
    try:
        execution = execute.submit(
            lease=lease, operation=payload.get("operation") or "",
            params=payload.get("params") or {},
            payload=jobs.tar_of(files) if files else None,
            timeout=payload.get("timeout"),
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
def job_logs(request, owner, uuid):
    """What the job has printed so far, from ``?offset=``.

    THE ENDPOINT FOR WATCHING, as distinct from `job_detail` (is it done?) and
    `job_output` (what did it produce?). A compile or a long script is exactly
    when somebody wants to see progress, and exactly when the finished-job
    endpoints have nothing to say.

    Answers WHILE THE JOB RUNS, which is the difference from `job_output` and
    is safe for the opposite reason: a partial log is obviously partial —
    `complete` says so and `offset` says where to resume — whereas a partial
    output file looks like a whole one.

    A junk offset starts from zero rather than 500ing. It arrives from a query
    string, so "abc" is a client's typo, and the endpoint whose whole job is to
    show somebody what went wrong is the worst place to answer a typo with a
    stack trace.
    """
    execution = _own_execution(owner, uuid)
    backend = runtime.get_backend()
    reader = getattr(backend, "execution_logs", None)
    if reader is None:
        return _error("this deployment's runtime cannot stream job output",
                      code="unsupported", status=501)
    try:
        offset = max(0, int(request.GET.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    slice_ = reader(execution, offset) or {}
    return JsonResponse({
        "uuid": str(execution.uuid),
        "text": slice_.get("text", ""),
        # Where to ask from next. Echoed back even on failure so a client can
        # keep its cursor rather than restarting the log from the top.
        "offset": slice_.get("offset", offset),
        # False when the slice was truncated: ask again NOW, not in a second.
        "complete": bool(slice_.get("complete", True)),
        "finished": execution.status in choices.FINISHED,
    })


# --------------------------------------------------------------------------- #
# Installs — a job that is watched                                            #
# --------------------------------------------------------------------------- #

def _own_install(owner, uuid) -> InstallRun:
    run = InstallRun.objects.filter(
        uuid=uuid, lease__owner=owner).select_related("lease", "execution").first()
    if run is None:
        raise Http404("no such install")
    return run


@require_http_methods(["GET", "POST"])
@token_required
def install_collection(request, owner, uuid):
    """``POST`` starts an install into this Capsule; ``GET`` lists recent ones.

    The body names `packages`, as "numpy+pandas" or as a list of names. Names
    only — the `dists` parameter refuses versions and operators, and says so.
    A Capsule reserved without internet access is refused with a sentence
    before anything starts (`install.NO_EGRESS`).
    """
    lease = _own_lease(owner, uuid)
    if request.method == "GET":
        runs = lease.installs.select_related("lease", "execution")[:20]
        return JsonResponse({"installs": [install.describe(r) for r in runs]})
    payload = _body(request)
    packages = payload.get("packages")
    if isinstance(packages, (list, tuple)):
        packages = "+".join(str(name) for name in packages)
    if not isinstance(packages, str) or not packages:
        return _error("`packages` must name at least one distribution, as "
                      "“numpy+pandas” or a list of names.", code="bad_packages")
    try:
        run = install.start(lease=lease, dists=packages, requested_by=owner,
                            timeout=payload.get("timeout"))
    except ValidationError as exc:
        return _refusal(exc)
    return JsonResponse(install.describe(run), status=201)


@require_GET
@token_required
def install_detail(request, owner, uuid):
    """The run, advanced: reading it is what pulls the next slice of log.

    ``?since=N`` returns the log from character N, so a client that already
    has the first N keeps its place; `log_length` in the body is where to ask
    from next. The whole copy is capped — `log_truncated` says when it was.
    """
    run = install.refresh(_own_install(owner, uuid))
    try:
        since = max(0, int(request.GET.get("since") or 0))
    except (TypeError, ValueError):
        since = 0
    body = install.describe(run)
    body["log"] = run.log[since:]
    body["log_since"] = min(since, len(run.log))
    return JsonResponse(body)


@require_POST
@token_required
def install_cancel(request, owner, uuid):
    run = install.cancel(_own_install(owner, uuid), actor=owner,
                         reason=str(_body(request).get("reason") or "")[:200])
    return JsonResponse(install.describe(run))


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
