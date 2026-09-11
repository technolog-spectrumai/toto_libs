"""The Compute Capsules desk: reserve, mount, watch, unmount.

One page and a handful of POST targets. The shape follows the platform's
convention — a page that renders derived state, JSON endpoints the page polls,
and every write re-checking permission server-side so a hand-posted form is
still refused.

**The shared secret never leaves this process.** The browser talks to Zenobia;
Zenobia signs and talks to the manager. That is the whole reason
``executor_backend`` exists on this side of the wire rather than the page
calling the manager directly.

Ownership is simple and strict: a Capsule belongs to the user who reserved it.
Staff can SEE the pool (they have to, to run the machine) but a Capsule is not a
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

from . import choices, conf, families, samples as samples_mod, services, transfer
from .limits import Limits, LimitsError
from .models import ComputeLease, Execution, CapsuleRuntime
from .runtime import RuntimeUnavailable, get_backend

log = logging.getLogger("toto.anastasia.views")


def _own_lease(request, uuid) -> ComputeLease:
    """A Capsule the requesting user actually holds.

    404 rather than 403 for somebody else's Capsule: whether a given uuid exists
    is not information a stranger needs, and a 403 answers that question.
    """
    # Filtered by owner in the QUERY, not checked after fetching: that is what
    # makes somebody else's Capsule indistinguishable from one that does not
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
    capsules = [services.capsule_report(lease) for lease in leases]
    report = services.pool_report()
    # Whether to offer the checkbox at all. A host that does not run the proxy
    # must not show a control whose only outcome is a refused mount.
    egress_offered = conf.egress_offered()

    # Only MOUNTED capsules are polled. An unmounted one has nothing to report,
    # and asking would wake the manager once every five seconds for nothing.
    poll_urls = {
        capsule["uuid"]: reverse("anastasia:status", args=[capsule["uuid"]])
        for capsule in capsules if capsule["state"] in choices.MOUNTED
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
        "capsules": capsules,
        "runtime_configured": bool(conf.executor_socket()),
        "files_supported": _files_supported(),
        **_vault_choices(request.user),
        "egress_offered": egress_offered,
        "max_capsules": conf.max_capsules_per_user(),
        "lease_days": conf.lease_days(),
        "held": len(leases),
        "poll_urls_json": json.dumps(poll_urls),
        "stale_seconds": conf.sample_stale_seconds(),
    }, request))


#: How many files the vault picker draws. `build_file_tree`'s own default,
#: named here because the page SAYS the number when it truncates — a tree that
#: silently stopped at 500 reads as "these are all your files".
VAULT_PICKER_LIMIT = 500

#: How many files one copy may move. The desk loops over the checked set, and
#: each file is a read and a write through the executor: an unbounded loop
#: would hold a worker for as long as somebody cared to tick boxes. Twenty-five
#: is well past what anyone picks by hand and well short of a request that
#: times out.
MAX_FILES_PER_TRANSFER = 25


def _files_supported() -> bool:
    return getattr(get_backend(), "capsule_files", None) is not None


def _vault_choices(user) -> dict:
    """What the copy-in picker offers: the person's own files, as a tree.

    `toto.vault.filetree.build_file_tree` with `vault/_file_tree.html` — the
    same bucket → folder → file picker the vault, OCR and the antivirus panel
    draw, rather than a fourth shape for the same act. Rows carry the file's
    **id**, which is what that partial's checkbox mode offers; the bearer API
    takes a `key` because a client outside this database has no id to use, and
    the desk has one.

    `include_public=False`: this copies a file INTO somebody's Capsule, which
    is an act on the file, not a read of it. Readable and mine are different
    questions and this is the second one.
    """
    from toto.vault.filetree import accessible_files, build_file_tree
    from toto.vault.models import Bucket

    mine = accessible_files(user, include_public=False).filter(owner=user)
    return {
        "vault_tree": build_file_tree(user, queryset=mine,
                                      limit=VAULT_PICKER_LIMIT),
        "vault_files_truncated": mine.count() > VAULT_PICKER_LIMIT,
        "vault_picker_limit": VAULT_PICKER_LIMIT,
        "max_files_per_transfer": MAX_FILES_PER_TRANSFER,
        "buckets": list(Bucket.objects.filter(owner=user)
                        .order_by("name").values("name", "slug")),
    }


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
        lease = services.reserve(
            owner=request.user,
            name=request.POST.get("name", ""),
            limits=limits, actor=request.user,
            # A checkbox is present-or-absent, so its mere presence is the
            # answer. Named explicitly rather than passed through from POST so
            # the form cannot set anything else on the lease.
            egress=bool(request.POST.get("egress")))
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
    shows what the Capsule IS doing rather than what it was doing when somebody
    last loaded it. A manager that does not answer leaves the previous sample
    in place with its age visible — see ``services.derive_state``, which turns
    a stale sample into DEGRADED rather than into a confident lie.
    """
    lease = _own_lease(request, uuid)
    services.refresh_runtime(lease)
    return JsonResponse(services.capsule_report(lease))


@login_required
def samples(request, uuid):
    """One Capsule's history, as JSON. The 9.2 endpoint.

    OWNER ONLY, through `_own_lease` like every other per-capsule route here:
    a series of how hard somebody's capsule has been working is theirs, and
    "somebody else's capsule is a 404" is the rule this app already follows.

    NO FILENAMES AND NO CONTENTS — `storage_bytes` and `storage_files` are
    counts, the boundary `executor/storage.py` draws and `samples.py` repeats.
    There is deliberately no parameter here that could widen this into a
    listing.
    """
    lease = _own_lease(request, uuid)
    try:
        hours = int(request.GET.get("hours", 24))
    except (TypeError, ValueError):
        # A junk window is 24 hours, not a 500. The caller is a chart.
        hours = 24
    return JsonResponse(samples_mod.series(lease, hours=hours))


# --------------------------------------------------------------------------- #
# The files area, from the desk                                                #
# --------------------------------------------------------------------------- #
#
# The page's door onto what `api.py` exposes to a bearer token. The listing is
# JSON the card fetches when its Files section is opened; the three writes are
# ordinary forms that redirect back to the card with a sentence. Nothing here
# decides anything the API does not: a name is judged by the executor, a
# vault file is looked up owner-filtered, and the copy out is metered by
# `transfer.to_bucket` — the same function, so the same price.

def _back_to_files(lease):
    """Back to the card, with its Files section open (the template reads the
    hash), so a person sees the result where they asked for it."""
    return redirect(reverse("anastasia:index") + f"#files-{lease.uuid}")


def _files_sentence(exc) -> str:
    """A refusal as one sentence. Three sources, one shape:
    `TransferRefused` (this side), `FilesRefused` (the executor's own words)
    and `RuntimeUnavailable` (nobody's fault, and must not read as the
    person's mistake)."""
    message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
    if isinstance(exc, RuntimeUnavailable):
        return _("the compute runtime is not answering (%(detail)s)") % {
            "detail": message}
    return message


def _files_error(request, lease, exc):
    messages.error(request, _files_sentence(exc))
    return _back_to_files(lease)


@login_required
def files(request, uuid):
    """The listing the card's Files section fetches. Owner-only JSON.

    `supported: false` when this deployment's runtime has no files area, and
    `complete: false` when the runtime did not answer or the walk stopped
    early — the card says which, rather than showing an empty area for a
    Capsule that is merely unreachable.
    """
    lease = _own_lease(request, uuid)
    lister = getattr(get_backend(), "capsule_files", None)
    if lister is None:
        return JsonResponse({"files": [], "complete": False, "supported": False})
    listing = lister(lease) or {}
    return JsonResponse({
        "files": listing.get("files", []),
        "complete": bool(listing.get("complete", False)),
        "supported": True,
    })


def _checked(request, field: str) -> list:
    """The checked set, bounded and deduplicated, order kept.

    Bounded HERE rather than in the template: the limit is what the server
    will do, and a form that can be re-posted by hand must be refused by the
    thing that acts, not by the thing that renders.
    """
    seen, out = set(), []
    for raw in request.POST.getlist(field):
        value = (raw or "").strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out[:MAX_FILES_PER_TRANSFER]


def _report(request, done: list, refused: list, sentence):
    """One success line naming the count, one error line naming each refusal.

    PER FILE, because a batch that reports only "3 of 5 copied" leaves the
    person to work out which two — and the two are exactly the ones they need
    to know about.
    """
    if done:
        messages.success(request, sentence(done))
    for name, why in refused:
        messages.error(request, _("%(name)s: %(why)s")
                       % {"name": name, "why": why})


@login_required
@require_POST
def file_from_vault(request, uuid):
    """Copy checked Vault files into the Capsule. Unmetered.

    A loop over the checked set rather than one file per request: the picker
    is a tree of checkboxes, and a person who ticked four boxes means four
    copies. Each is still judged on its own — `transfer.to_capsule` per file,
    the executor's name rule per name — so one refusal stops that file and
    nothing else.
    """
    from toto.vault.models import VaultFile

    from .executor_backend import FilesRefused

    lease = _own_lease(request, uuid)
    ids = _checked(request, "file")
    if not ids:
        messages.error(request, _("Pick at least one file to copy in."))
        return _back_to_files(lease)

    # Owner-filtered in the QUERY: somebody else's file and no file at all are
    # the same absence, as `_own_lease` makes them for a Capsule. A picked id
    # that is not the caller's simply is not in this map.
    wanted = {str(f.pk): f for f in
              VaultFile.objects.filter(owner=request.user, pk__in=[
                  i for i in ids if i.isdigit()])}
    replace = bool(request.POST.get("replace"))
    # A name is only meaningful for ONE file: giving four files one name would
    # write four files over each other.
    name = (request.POST.get("name") or "").strip() if len(ids) == 1 else ""

    done, refused = [], []
    for picked in ids:
        vault_file = wanted.get(picked)
        if vault_file is None:
            refused.append((picked, _("no such file")))
            continue
        try:
            transfer.to_capsule(lease=lease, vault_file=vault_file, name=name,
                                actor=request.user, replace=replace)
        except (transfer.TransferRefused, FilesRefused,
                RuntimeUnavailable) as exc:
            refused.append((vault_file.title, _files_sentence(exc)))
        else:
            done.append(vault_file.title)

    _report(request, done, refused, lambda names: _(
        "Copied into the Capsule: %(names)s.") % {"names": ", ".join(names)})
    return _back_to_files(lease)


@login_required
@require_POST
def file_to_vault(request, uuid):
    """Copy checked Capsule files into one of the person's buckets.

    METERED AND SCANNED like an upload — `transfer.to_bucket` is the one
    function that does it, for this door and the API's. Each file is charged
    on its own, so a refusal part way through leaves the copies already made
    paid for and the rest not made.
    """
    from toto.vault.models import Bucket

    from .executor_backend import FilesRefused

    lease = _own_lease(request, uuid)
    names = _checked(request, "name")
    if not names:
        messages.error(request, _("Pick at least one file to copy out."))
        return _back_to_files(lease)
    bucket = get_object_or_404(
        Bucket, owner=request.user,
        slug=(request.POST.get("bucket") or "").strip())
    title = (request.POST.get("title") or "").strip() if len(names) == 1 else ""

    done, refused = [], []
    for name in names:
        try:
            vault_file = transfer.to_bucket(
                lease=lease, name=name, bucket=bucket, actor=request.user,
                title=title)
        except (transfer.TransferRefused, FilesRefused,
                RuntimeUnavailable) as exc:
            refused.append((name, _files_sentence(exc)))
        else:
            done.append(vault_file.title)

    _report(request, done, refused, lambda titles: _(
        "Copied into “%(bucket)s”: %(names)s.")
        % {"bucket": bucket.name, "names": ", ".join(titles)})
    return _back_to_files(lease)


@login_required
@require_POST
def file_delete(request, uuid):
    from .executor_backend import FilesRefused

    lease = _own_lease(request, uuid)
    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, _("Name the file to delete."))
        return _back_to_files(lease)
    try:
        get_backend().capsule_file_delete(lease, name)
    except (FilesRefused, RuntimeUnavailable) as exc:
        return _files_error(request, lease, exc)
    except AttributeError:
        messages.error(request, _(
            "This deployment's runtime cannot hold files in a Capsule."))
        return _back_to_files(lease)
    messages.success(request, _("%(name)s is deleted from the Capsule.")
                     % {"name": name})
    return _back_to_files(lease)


@login_required
def pool(request):
    return JsonResponse(services.pool_report())


# --------------------------------------------------------------------------- #
# The operator's page                                                          #
# --------------------------------------------------------------------------- #
# Separate from the Capsule desk, and staff-only, because the questions differ.
# A user asks "can I run my job"; an operator asks "what is this machine doing,
# and how do I stop it". The second needs the whole pool, every live job across
# every owner, and the two switches — none of which belongs on a page a user
# sees.


# --------------------------------------------------------------------------- #
# API tokens                                                                   #
# --------------------------------------------------------------------------- #
# WITHOUT THESE THREE VIEWS THE API IS UNREACHABLE. `CapsuleToken.issue()` had
# no caller but its own tests until 2026-09-10: no route, no admin
# registration, no management command. Everything at `/api/v1/` authenticated
# with a bearer token that a person had no way to obtain, which made a tested,
# documented, mounted API a feature nobody could use. That is a worse failure
# than a missing endpoint, because every individual piece looks finished.

#: How many live tokens one person may hold. Not a security boundary — a token
#: acts as its owner, so a second one grants nothing a first does not — but a
#: list nobody can read is a list nobody revokes from, and "which of these 40
#: is my old laptop" is how a stale credential survives.
MAX_TOKENS_PER_USER = 10


@login_required
def tokens(request):
    """The tokens this person holds, and the form that mints one.

    Revoked ones are listed too, greyed: "I revoked that yesterday" is a thing
    a person needs to confirm, and a row that vanishes cannot confirm it.
    """
    from .tokens import CapsuleToken

    rows = list(CapsuleToken.objects.filter(owner=request.user)
                .order_by("revoked_at", "-created_at"))
    return render(request, "anastasia/tokens.html", PageProcessor().decorate({
        "tokens": rows,
        "live_count": sum(1 for row in rows if row.is_live),
        "max_tokens": MAX_TOKENS_PER_USER,
        # Shown ONCE, carried in the session by `token_issue` below rather than
        # in the URL — a query string lands in browser history, in the server
        # log and in any proxy between the two.
        "fresh_token": request.session.pop("anastasia_fresh_token", ""),
        "api_root": request.build_absolute_uri("/api/v1/"),
    }, request))


@login_required
@require_POST
def token_issue(request):
    """Mint one. The raw value is shown once and never again."""
    from .tokens import CapsuleToken

    label = (request.POST.get("label") or "").strip()
    if not label:
        messages.error(request, _("Give the token a label, so you can tell "
                                  "which client it belongs to when you come "
                                  "to revoke it."))
        return redirect("anastasia:tokens")
    if len(label) > 120:
        label = label[:120]

    live = CapsuleToken.objects.live().filter(owner=request.user).count()
    if live >= MAX_TOKENS_PER_USER:
        messages.error(request, _(
            "You already hold %(count)s API tokens, which is the limit. "
            "Revoke one you no longer use.") % {"count": live})
        return redirect("anastasia:tokens")

    _row, raw = CapsuleToken.issue(owner=request.user, label=label)
    # The session, not the template context of a redirect target reached by
    # GET: a POST that renders its own page cannot be reloaded without
    # re-posting, and a person WILL reload the page holding the only copy of
    # their token.
    request.session["anastasia_fresh_token"] = raw
    return redirect("anastasia:tokens")


@login_required
@require_POST
def token_revoke(request, pk):
    """Revoke one of YOUR tokens. Somebody else's is a 404, as everywhere."""
    from .tokens import CapsuleToken

    row = get_object_or_404(CapsuleToken, pk=pk, owner=request.user)
    if row.is_live:
        row.revoke()
        messages.success(request, _("“%(label)s” can no longer be used.")
                         % {"label": row.label})
    return redirect("anastasia:tokens")


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
        "mounted": (CapsuleRuntime.objects.filter(state=choices.READY)
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
