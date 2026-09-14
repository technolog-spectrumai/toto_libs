"""The Compute Capsules desk: reserve, mount, watch, unmount.

A LIST AND A VIEW. `index` is the person's Capsules as cards, with the pool
above them and Reserve behind a modal; each card opens the Capsule's own view,
one URL per tab (Information, History, Files), the way every tab strip on the
platform works — a reload keeps its place and a POST's message lands on the tab
that produced it. It was one long page until 2026-09-14, and every section it
grew (history, files) made the next Capsule further away.

The shape otherwise follows the platform's convention — pages that render
derived state, JSON endpoints the pages poll, and every write re-checking
permission server-side so a hand-posted form is still refused.

**The shared secret never leaves this process.** The browser talks to Zenobia;
Zenobia signs and talks to the manager. That is the whole reason
``executor_backend`` exists on this side of the wire rather than the page
calling the manager directly.

Ownership is simple and strict: a Capsule belongs to the user who reserved it.
Staff can SEE the pool (they have to, to run the machine) but a Capsule is not a
shared resource and there is no borrowing.
"""

from __future__ import annotations

import io
import itertools
import json
import logging
import math
import os
import re
from datetime import datetime, timezone as dt_timezone

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext
from django.views.decorators.http import require_GET, require_POST

from toto.ui import PageProcessor

from . import choices, conf, families, install, samples as samples_mod, services, transfer
from .limits import Limits, LimitsError
from .models import CapsuleEvent, ComputeLease, Execution, CapsuleRuntime, InstallRun
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


def _own_open_lease(request, uuid) -> ComputeLease:
    """`_own_lease`, and still reserved.

    The Capsule VIEW is for capacity somebody holds. A released reservation
    is not in the list, cannot be mounted and has no files area, so its tabs
    would be a page of refusals; it is a 404 like any Capsule this person
    does not hold. The verbs keep `_own_lease` — releasing twice is an
    ordinary outcome, not a missing page.
    """
    return get_object_or_404(ComputeLease.objects.open(), uuid=uuid,
                             owner=request.user)


#: The Capsule view's tabs: (slug, label, icon, url name). ONE URL PER TAB,
#: rendered by the server — the idiom of every tab strip on the platform
#: (antivirus, vault, forum rooms, company). Lazy labels, because this tuple
#: is built at import, in whatever language the process started in.
CAPSULE_TABS = (
    ("information", gettext_lazy("Information"), "fa-solid fa-circle-info",
     "anastasia:capsule"),
    ("history", gettext_lazy("History"), "fa-solid fa-chart-line",
     "anastasia:capsule_history"),
    ("files", gettext_lazy("Files"), "fa-solid fa-folder-tree",
     "anastasia:capsule_files"),
    ("env", gettext_lazy("Env"), "fa-solid fa-cubes",
     "anastasia:capsule_env"),
)
_TAB_URLS = {slug: url for slug, _label, _icon, url in CAPSULE_TABS}


def _tab_from(request) -> str:
    """Which tab a verb was pressed on. Validated against the tab list, so a
    hand-posted value can only ever choose among real pages."""
    tab = (request.POST.get("tab") or "").strip()
    return tab if tab in _TAB_URLS else "information"


def _back(lease, tab: str = "information"):
    """Back to the tab the person was on, where the message belongs."""
    return redirect(reverse(_TAB_URLS.get(tab, "anastasia:capsule"),
                            args=[lease.uuid]))


def _refusal(request, exc, lease=None, tab: str = "information"):
    """One refusal, rendered the way the caller asked for it."""
    message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
    code = getattr(exc, "refusal_code", "")
    if request.headers.get("Accept", "").startswith("application/json"):
        return JsonResponse({"error": message, "code": code}, status=409)
    messages.error(request, message)
    if lease is not None:
        return _back(lease, tab)
    return redirect("anastasia:index")


#: What the reserve modal starts with. The same numbers the form always
#: offered; a refusal re-renders the modal with what was TYPED instead.
RESERVE_DEFAULTS = {"name": "", "cpu_millicores": 1000, "ram_mb": 1024,
                    "scratch_mb": 512, "pids": 128, "egress": False}


def _index_context(request, **extra) -> dict:
    """Everything the list page draws. Shared by `index` and by a refused
    `reserve`, which re-renders this page with its modal open."""
    leases = list(ComputeLease.objects.open()
                  .filter(owner=request.user).select_related("runtime"))
    capsules = [services.capsule_report(lease) for lease in leases]
    for capsule in capsules:
        capsule["url"] = reverse("anastasia:capsule", args=[capsule["uuid"]])
    report = services.pool_report()
    held = len(leases)
    max_capsules = conf.max_capsules_per_user()

    # Only MOUNTED capsules are polled. An unmounted one has nothing to report,
    # and asking would wake the manager once every five seconds for nothing.
    poll_urls = {
        capsule["uuid"]: reverse("anastasia:status", args=[capsule["uuid"]])
        for capsule in capsules if capsule["state"] in choices.MOUNTED
    }
    return {
        "pool": report,
        "pool_rows": _pool_rows(report),
        "capsules": capsules,
        "runtime_configured": bool(conf.executor_socket()),
        # Whether to offer the checkbox at all. A host that does not run the
        # proxy must not show a control whose only outcome is a refused mount.
        "egress_offered": conf.egress_offered(),
        "max_capsules": max_capsules,
        "lease_days": conf.lease_days(),
        "held": held,
        # The button is disabled WITH A SENTENCE when this is false, never
        # hidden: a person looking for Reserve must find out why it is grey.
        "can_reserve": bool(report.get("configured")) and held < max_capsules,
        "poll_urls_json": json.dumps(poll_urls),
        "stale_seconds": conf.sample_stale_seconds(),
        "open_modal": "",
        "reserve": dict(RESERVE_DEFAULTS),
        "reserve_error": "",
        **extra,
    }


@login_required
def index(request):
    """Everything a person needs to decide whether to reserve, and the door
    into each Capsule they hold.

    The pool report is shown to everyone, not just staff: "reserve" is a choice
    made against a number, and hiding the number turns a refusal into a
    mystery.
    """
    # PageProcessor, like every other page on the platform. It is not
    # decoration: oya/base.html builds the Tailwind palette from
    # `theme.theme.colors`, and with no theme in the context that expression
    # renders `{}` — so every custom colour class silently stops existing.
    # This page shipped without it and the result was a desk that ignored the
    # dark-mode toggle, drew its cards with default hairline borders, and
    # rendered the Reserve button as white text on a background that was never
    # applied: present, clickable, invisible. base.html warns about exactly
    # this in the comment above that `colors:` line.
    return render(request, "anastasia/index.html",
                  PageProcessor().decorate(_index_context(request), request))


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
    """Book a Capsule from the modal.

    A REFUSAL RE-RENDERS THE LIST WITH THE MODAL OPEN, holding what was typed
    and the sentence that refused it — `company/_modal.html`'s shape. The
    redirect-and-toast this replaced threw away five numbers and a name, and
    the toast hid itself after six seconds, so the person was left with an
    empty form and a message they may not have read.
    """
    typed = {
        "name": (request.POST.get("name") or "")[:60],
        "cpu_millicores": request.POST.get("cpu_millicores", ""),
        "ram_mb": request.POST.get("ram_mb", ""),
        "scratch_mb": request.POST.get("scratch_mb", ""),
        "pids": request.POST.get("pids", ""),
        "egress": bool(request.POST.get("egress")),
    }

    def refuse(exc):
        if request.headers.get("Accept", "").startswith("application/json"):
            return _refusal(request, exc)
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        return render(request, "anastasia/index.html", PageProcessor().decorate(
            _index_context(request, open_modal="reserve", reserve=typed,
                           reserve_error=message), request))

    try:
        limits = Limits.from_mapping({
            "cpu_millicores": int(request.POST.get("cpu_millicores") or 0),
            "ram_mb": int(request.POST.get("ram_mb") or 0),
            "scratch_mb": int(request.POST.get("scratch_mb") or 0),
            "pids": int(request.POST.get("pids") or 0),
        })
    except (TypeError, ValueError, LimitsError) as exc:
        return refuse(ValidationError(
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
        return refuse(exc)

    messages.success(request, _(
        "“%(name)s” is reserved. Mount it when you want to use it — the "
        "capacity is yours either way.") % {"name": lease.name})
    return redirect("anastasia:capsule", uuid=lease.uuid)


@login_required
@require_POST
def mount(request, uuid):
    lease = _own_lease(request, uuid)
    tab = _tab_from(request)
    try:
        services.mount(lease=lease, actor=request.user)
    except ValidationError as exc:
        return _refusal(request, exc, lease, tab)
    return _back(lease, tab)


@login_required
@require_POST
def unmount(request, uuid):
    lease = _own_lease(request, uuid)
    services.unmount(lease=lease, actor=request.user, reason="unmounted by owner")
    return _back(lease, _tab_from(request))


@login_required
@require_POST
def release(request, uuid):
    """Give the capacity back for good. Unmounts on the way out.

    Back to the LIST, not to a tab: a released Capsule has no view (see
    `_own_open_lease`), and the list is where its absence is the news.
    """
    lease = _own_lease(request, uuid)
    services.release(lease=lease, reason="released by owner", actor=request.user)
    messages.success(request, _(
        "“%(name)s” is released and its capacity is back in the pool.")
        % {"name": lease.name})
    return redirect("anastasia:index")


# --------------------------------------------------------------------------- #
# The Capsule view                                                             #
# --------------------------------------------------------------------------- #

#: How many events and jobs the History tab lists. The newest, and the page
#: says so: a list that silently stopped would read as the whole history.
HISTORY_ROWS = 50


def _capsule_context(request, lease, active_tab: str, **extra) -> dict:
    """What every tab draws: the header, its verbs, and the strip."""
    report = services.capsule_report(lease)
    mounted = report["state"] in choices.MOUNTED
    return {
        "lease": lease,
        "capsule": report,
        "mounted": mounted,
        "active_tab": active_tab,
        "tabs": [
            {"slug": slug, "label": label, "icon": icon, "active": slug == active_tab,
             "url": reverse(url, args=[lease.uuid])}
            for slug, label, icon, url in CAPSULE_TABS
        ],
        "status_url": reverse("anastasia:status", args=[lease.uuid]),
        "stale_seconds": conf.sample_stale_seconds(),
        "runtime_configured": bool(conf.executor_socket()),
        **extra,
    }


def _render_tab(request, template: str, context: dict):
    return render(request, template, PageProcessor().decorate(context, request))


@login_required
def capsule(request, uuid):
    """INFORMATION — what the Capsule is now.

    `created_at` and `expires_at` had never been drawn: the desk said a
    reservation lasts N days and never said when this one ends.
    """
    lease = _own_open_lease(request, uuid)
    storage_supported = getattr(get_backend(), "storage", None) is not None
    return _render_tab(request, "anastasia/capsule_information.html",
                       _capsule_context(
                           request, lease, "information",
                           runtime=services.runtime_for(lease),
                           storage_url=(reverse("anastasia:storage",
                                                args=[lease.uuid])
                                        if storage_supported else "")))


@login_required
def capsule_history(request, uuid):
    """HISTORY — what the Capsule did: its readings, its events, its jobs."""
    lease = _own_open_lease(request, uuid)
    events = (CapsuleEvent.objects.filter(lease=lease)
              .select_related("actor").order_by("-created_at", "-pk")
              [:HISTORY_ROWS])
    jobs_rows = []
    for execution in (Execution.objects.filter(lease=lease)
                      .order_by("-created_at", "-pk")[:HISTORY_ROWS]):
        seconds = None
        if execution.started_at and execution.finished_at:
            seconds = round((execution.finished_at
                             - execution.started_at).total_seconds())
        jobs_rows.append({"execution": execution, "seconds": seconds})
    return _render_tab(request, "anastasia/capsule_history.html",
                       _capsule_context(
                           request, lease, "history",
                           samples_url=reverse("anastasia:samples",
                                               args=[lease.uuid]),
                           events=[{"event": event,
                                    "reason": (event.detail or {}).get("reason", "")}
                                   for event in events],
                           jobs=jobs_rows,
                           history_rows=HISTORY_ROWS))


@login_required
def storage(request, uuid):
    """How much disk the Capsule holds, per area. COUNTS ONLY — the boundary
    `executor/storage.py` draws. The Information tab asks once on load, not
    on every poll: the answer costs the executor a walk."""
    lease = _own_lease(request, uuid)
    reading = getattr(get_backend(), "storage", None)
    if reading is None:
        return JsonResponse({"supported": False, "complete": False})
    body = reading(lease) or {}
    return JsonResponse({**body, "supported": True,
                         "complete": bool(body.get("complete", False))})


# --------------------------------------------------------------------------- #
# Env — packages installed into the Capsule                                    #
# --------------------------------------------------------------------------- #
#
# The page's door onto `install.py`, which the bearer API already drives. Two
# kinds in one tab, because what a person does with either is the same: name
# packages, watch a phase and a count, read the log if it failed. What differs
# — where the files go, which operation runs, what a name may look like — is
# `install.KINDS`, and none of it is decided here.

#: How many runs of each kind the tab lists. The newest; the page says so.
ENV_RUNS = 10

#: A wheel's installed metadata directory: `<name>-<version>.dist-info`, with
#: the name's dashes already written as underscores and no dash in the version.
_DIST_INFO = re.compile(r"^site-packages/([^/]+)-([^/-]+)\.dist-info(?:/|$)")

#: The runner's record of one installed CTAN package.
_TEXMF_MARKER = re.compile(r"^texmf/\.toto-installed/([a-z0-9][a-z0-9-]*)$")


def _installed_python(entries) -> list:
    """(name, version) for every distribution in site-packages.

    READ OFF THE LISTING, not asked of pip: running a job to find out what is
    installed would cost a runner per page view. A `.dist-info` directory is
    what pip leaves for every distribution it installs, dependencies
    included, so this is the whole set — and a listing that stopped early
    says so beside it.
    """
    found = set()
    for entry in entries or ():
        match = _DIST_INFO.match((entry or {}).get("name") or "")
        if match:
            found.add((match.group(1).replace("_", "-"), match.group(2)))
    return sorted(found)


def _installed_latex(entries) -> list:
    """Every CTAN package `anastasia-install-latex` recorded as installed."""
    found = set()
    for entry in entries or ():
        match = _TEXMF_MARKER.match((entry or {}).get("name") or "")
        if match and not entry.get("is_dir"):
            found.add(match.group(1))
    return sorted(found)


def _back_to_env(lease):
    return _back(lease, "env")


@login_required
def capsule_env(request, uuid):
    """ENV — what is installed into the Capsule, and installing more.

    Installing needs the Capsule MOUNTED (it is a job) and reserved WITH
    internet access (it fetches). Neither is hidden when missing: the form is
    drawn disabled with the sentence that says which, because a tab that
    simply had no form would leave a person looking for it.
    """
    lease = _own_open_lease(request, uuid)
    context = _capsule_context(request, lease, "env")
    backend = get_backend()
    listing = {}
    if context["mounted"] and getattr(backend, "capsule_files", None) is not None:
        listing = backend.capsule_files(lease) or {}
    entries = listing.get("files", [])

    runs = {}
    status_urls = {}
    for kind in install.KINDS:
        rows = (InstallRun.objects.filter(lease=lease, kind=kind)
                .select_related("lease", "execution")[:ENV_RUNS])
        runs[kind] = []
        for row in rows:
            described = install.describe(row)
            described["status_url"] = reverse(
                "anastasia:env_install_status", args=[lease.uuid, row.uuid])
            described["cancel_url"] = reverse(
                "anastasia:env_install_cancel", args=[lease.uuid, row.uuid])
            runs[kind].append(described)
            # Only OPEN runs are polled; a finished one has nothing new to say.
            if not described["finished"]:
                status_urls[described["uuid"]] = described["status_url"]

    context.update({
        "egress": bool(lease.egress),
        "egress_offered": conf.egress_offered(),
        "ctan_mirror_set": bool(conf.ctan_mirror()),
        "python_runs": runs["python"],
        "latex_runs": runs["latex"],
        "env_runs": ENV_RUNS,
        "status_urls": status_urls,
        "installed_python": _installed_python(entries),
        "installed_latex": _installed_latex(entries),
        "listing_answered": bool(listing),
        "listing_complete": bool(listing.get("complete", False)),
    })
    return _render_tab(request, "anastasia/capsule_env.html", context)


@login_required
@require_POST
def env_install(request, uuid):
    """Start an install from the tab. Every refusal is `install.start`'s own
    sentence — no internet access, no mirror, a version in a name, a Capsule
    that is not mounted — shown on the tab it came from."""
    lease = _own_lease(request, uuid)
    kind = (request.POST.get("kind") or "").strip()
    # Spaces, commas or new lines between names: what a person types or
    # pastes. The operation's parameter is the one rule for what a NAME may be.
    names = [name for name in re.split(r"[\s,]+", request.POST.get("packages") or "")
             if name]
    if not names:
        messages.error(request, _("Name at least one package to install."))
        return _back_to_env(lease)
    try:
        run = install.start(lease=lease, dists="+".join(names), kind=kind,
                            requested_by=request.user)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return _back_to_env(lease)
    messages.success(request, _("Installing %(names)s. You can leave this "
                                "page; the install carries on.")
                     % {"names": ", ".join(run.packages)})
    return _back_to_env(lease)


def _own_install(request, uuid, run) -> InstallRun:
    lease = _own_lease(request, uuid)
    return get_object_or_404(
        InstallRun.objects.select_related("lease", "execution"),
        uuid=run, lease=lease)


@login_required
@require_GET
def env_install_status(request, uuid, run):
    """One run, advanced — reading it pulls the next slice of its log.

    `?since=N` returns the log from character N, the API's contract, so the
    tab appends rather than redraws.
    """
    row = install.refresh(_own_install(request, uuid, run))
    try:
        since = max(0, int(request.GET.get("since") or 0))
    except (TypeError, ValueError):
        since = 0
    body = install.describe(row)
    body["log"] = row.log[since:]
    body["log_since"] = min(since, len(row.log))
    return JsonResponse(body)


@login_required
@require_POST
def env_install_cancel(request, uuid, run):
    row = _own_install(request, uuid, run)
    install.cancel(row, actor=request.user,
                   reason=_("Stopped from the Env tab."))
    messages.success(request, _("The install was stopped."))
    return _back_to_env(row.lease)


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


@login_required
@require_POST
def take_reading(request, uuid):
    """Take a reading NOW, from the History tab's button.

    `samples.take` — the beat's own function, so a reading taken by hand is
    the same reading the beat would have taken: refreshed, storage included,
    NULL where unmeasured. THE THROTTLE STILL HOLDS. A press within five
    minutes of the last reading writes nothing and says when the next one can
    be taken; a button that bypassed it would be the way to fill the table at
    one row a second.

    Not an error when there is nothing to read: an unmounted Capsule answers
    200 with a reason, because "nothing to read" is an ordinary answer and the
    page shows it as a sentence.
    """
    lease = _own_open_lease(request, uuid)
    taken = samples_mod.take(lease)
    if taken["recorded"]:
        sentence = _("A reading was taken.")
    elif taken["reason"] == samples_mod.NOT_MOUNTED:
        sentence = _("This Capsule is not mounted, so there is nothing to read.")
    else:
        minutes = max(1, math.ceil(taken["next_in_seconds"] / 60))
        sentence = ngettext(
            "Readings are at most one every %(interval)s minutes. The next can "
            "be taken in %(minutes)s minute.",
            "Readings are at most one every %(interval)s minutes. The next can "
            "be taken in %(minutes)s minutes.",
            minutes) % {"interval": samples_mod.MIN_INTERVAL_SECONDS // 60,
                        "minutes": minutes}

    if request.headers.get("Accept", "").startswith("application/json"):
        return JsonResponse({
            "recorded": taken["recorded"],
            "reason": taken["reason"],
            "next_in_seconds": taken["next_in_seconds"],
            "message": sentence,
        })
    # Without JavaScript the button is an ordinary form: back to the tab it
    # was pressed on, with the same sentence.
    if taken["recorded"]:
        messages.success(request, sentence)
    else:
        messages.info(request, sentence)
    return _back(lease, "history")


# --------------------------------------------------------------------------- #
# The files area, from the desk                                                #
# --------------------------------------------------------------------------- #
#
# The Files tab is the page's door onto what `api.py` exposes to a bearer
# token. It draws the area the way the Vault draws a bucket — the same rows,
# the same search, sort and tree/grid switch, copied from
# `vault/public_file_list.html` because toto-base is pull-only and has no
# partial to include — with the Vault's buttons that mean nothing in a Capsule
# left out and the two transfers added. Every write is an ordinary form that
# redirects back to the tab with a sentence. Nothing here decides anything the
# API does not: a name is judged by the executor, a vault file is looked up
# owner-filtered, and the copy out is metered by `transfer.to_bucket` — the
# same function, so the same price.

#: The most one uploaded file may carry. The API's `MAX_TRANSFER_BYTES`, for
#: the API's reason: this process holds the bytes before the executor sees
#: them, and the executor's own budget does not protect the web tier. A test
#: pins the two equal.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024

#: Extension -> the Vault's type vocabulary, so the Files tab draws the same
#: icon a Vault row would. Presentation only: the executor never learns a type.
_FILE_TYPES = {
    "pdf": "pdf", "png": "image", "jpg": "image", "jpeg": "image",
    "gif": "image", "webp": "image", "bmp": "image", "svg": "svg",
    "mp3": "audio", "wav": "audio", "ogg": "audio", "flac": "audio",
    "mp4": "video", "mkv": "video", "webm": "video", "mov": "video",
    "txt": "text", "md": "text", "log": "text", "rst": "text",
    "json": "json", "yaml": "yaml", "yml": "yaml", "csv": "csv",
    "html": "html", "htm": "html", "xml": "xml",
    "tex": "latex", "sty": "latex", "cls": "latex", "bib": "bib",
    "py": "python", "ipynb": "json",
    "zip": "zip", "gz": "zip", "tgz": "zip", "tar": "zip", "whl": "zip",
}

#: What may be shown INLINE, and as what. Raster images only: an SVG or an
#: HTML file served inline from this origin is a script served from this
#: origin, written by a runner. Everything else is an attachment.
_INLINE_IMAGES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                  "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"}


def _back_to_files(lease):
    """Back to the Files tab, where the person asked for it."""
    return _back(lease, "files")


def _extension(leaf: str) -> str:
    return leaf.rpartition(".")[2].lower() if "." in leaf else ""


def _file_row(entry: dict, depth: int, pid, row_id: int) -> dict:
    name = entry["name"]
    leaf = name.rpartition("/")[2]
    ext = _extension(leaf)
    try:
        stamp = int(entry.get("modified") or 0)
    except (TypeError, ValueError):
        stamp = 0
    return {
        "t": "file", "id": row_id, "pid": pid, "depth": depth,
        "title": leaf, "path": name,
        "size": int(entry.get("size") or 0),
        "ext": ext, "file_type": _FILE_TYPES.get(ext, "file"),
        # The Vault's date filter compares ISO date strings, so this is one.
        # Empty for a file the executor could not stat — "unknown", never 1970.
        "modified": (datetime.fromtimestamp(stamp, tz=dt_timezone.utc)
                     .date().isoformat() if stamp else ""),
        "viewable": ext in _INLINE_IMAGES,
    }


def _vault_rows(entries) -> list:
    """The executor's flat listing, as the Vault's depth-first rows.

    THE VAULT'S SHAPE, BUILT HERE rather than a second tree idea in the
    browser: a pre-ordered list where every row carries its depth and its
    parent's id, each folder followed by its subfolders and then its own
    files, the area's root files last (`vault/views.py:_build_flat_items`).
    The browser code copied from the Vault then works unchanged.

    Folders the listing did not name are made from their files' paths, so a
    file never hangs from a parent that does not exist; an empty folder the
    listing did name is a row with nothing under it. Ids come from ONE
    counter, so a folder and a file never share one — the collision that once
    highlighted a folder when a file was opened.
    """
    counter = itertools.count(1)
    dirs: dict = {}
    children: dict = {}
    files_under: dict = {}

    def ensure(path: str) -> None:
        if not path or path in dirs:
            return
        parent = path.rpartition("/")[0]
        ensure(parent)
        dirs[path] = next(counter)
        children.setdefault(parent, []).append(path)

    for entry in entries or ():
        name = (entry or {}).get("name") or ""
        if not name:
            continue
        if entry.get("is_dir"):
            ensure(name)
        else:
            parent = name.rpartition("/")[0]
            ensure(parent)
            files_under.setdefault(parent, []).append(entry)

    rows: list = []

    def emit(path: str, depth: int, pid) -> None:
        for sub in sorted(children.get(path, ())):
            rows.append({
                "t": "dir", "id": dirs[sub], "pid": pid, "depth": depth,
                "name": sub.rpartition("/")[2], "path": sub,
                "n_dirs": len(children.get(sub, ())),
                "n_files": len(files_under.get(sub, ())),
            })
            emit(sub, depth + 1, dirs[sub])
        for entry in sorted(files_under.get(path, ()), key=lambda e: e["name"]):
            rows.append(_file_row(entry, depth, pid, next(counter)))

    emit("", 0, None)
    return rows


def _area_budget():
    """The executor's total bound on a files area, for the gauge — or None.

    Read from the executor's own constant rather than restated, so the gauge
    cannot promise a number the executor does not enforce. The import is
    lazy and forgiving: the gauge is information, not a precondition.
    """
    try:
        from .executor.capsules import DEFAULT_AREA_BUDGET
    except Exception:  # noqa: BLE001
        return None
    return DEFAULT_AREA_BUDGET


@login_required
def capsule_files(request, uuid):
    """FILES — the Capsule's kept area, drawn like a Vault bucket.

    Listed only while MOUNTED: the area exists between mounts, but reading it
    needs the executor, and a tab that could only refuse would be worse than
    one that says to mount first.
    """
    lease = _own_open_lease(request, uuid)
    context = _capsule_context(request, lease, "files")
    backend = get_backend()
    supported = getattr(backend, "capsule_files", None) is not None
    listing = {}
    if supported and context["mounted"]:
        listing = backend.capsule_files(lease) or {}
    rows = _vault_rows(listing.get("files", []))
    held = sum(row["size"] for row in rows if row["t"] == "file")
    budget = _area_budget()
    context.update({
        "files_supported": supported,
        # An empty dict is "nobody answered", which is not "nothing there".
        "answered": bool(listing),
        "complete": bool(listing.get("complete", False)),
        "items": rows,
        "folders": [row["path"] for row in rows if row["t"] == "dir"],
        "held_bytes": held,
        "area_budget": budget,
        "held_percent": min(100, round(held * 100 / budget)) if budget else 0,
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        **_vault_choices(request.user),
    })
    return _render_tab(request, "anastasia/capsule_files.html", context)


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
def files_list(request, uuid):
    """The raw listing, as owner-only JSON.

    `supported: false` when this deployment's runtime has no files area, and
    `complete: false` when the runtime did not answer or the walk stopped
    early — "nobody answered" and "it is empty" are different claims.
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


@login_required
@require_GET
def file_download(request, uuid):
    """One file's bytes, as an attachment — or inline, for a raster image.

    THE NAME IS IN THE QUERY STRING HERE, and that is not the API's rule
    broken. The API keeps names out of the PATH, where a name legal in the
    area can be illegal or mean something else; a query value is decoded by
    Django to exactly the string that was encoded, and a browser can only
    follow a link or fill an `<img src>` with a GET.

    INLINE ONLY FOR RASTER IMAGES (`_INLINE_IMAGES`). These bytes were
    written by a runner; served inline from this origin, an SVG or an HTML
    file is script running as this site. Everything else is
    `application/octet-stream` as an attachment, with `nosniff` so the
    browser cannot decide otherwise, and a sandboxing CSP for anyone who
    navigates to the URL directly.
    """
    from .executor_backend import FilesRefused

    lease = _own_lease(request, uuid)
    name = (request.GET.get("name") or "").strip()
    reader = getattr(get_backend(), "capsule_file_read", None)
    if not name or reader is None:
        raise Http404("no such file")
    try:
        data = reader(lease, name)
    except (FilesRefused, RuntimeUnavailable) as exc:
        return _files_error(request, lease, exc)

    leaf = name.rpartition("/")[2] or "file"
    inline_type = _INLINE_IMAGES.get(_extension(leaf))
    inline = request.GET.get("view") == "1" and inline_type is not None
    response = FileResponse(
        io.BytesIO(data), as_attachment=not inline, filename=leaf,
        content_type=inline_type if inline else "application/octet-stream")
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return response


@login_required
@require_POST
def file_upload(request, uuid):
    """Put files from this computer into a folder of the Capsule.

    The one way into a Capsule that is not a copy from the Vault, and for the
    same reason the Vault has a gateway: a file on a laptop should not have
    to be uploaded to durable storage first just to be worked on. NOT METERED,
    like a copy in: nothing durable is created, and the area is capacity the
    person already holds (and bounded — the executor refuses a write past the
    area budget with its own sentence).

    Per file, like the transfers: one refusal names that file and stops
    nothing else.
    """
    from .executor_backend import FilesRefused

    lease = _own_lease(request, uuid)
    writer = getattr(get_backend(), "capsule_file_write", None)
    if writer is None:
        messages.error(request, _(
            "This deployment's runtime cannot hold files in a Capsule."))
        return _back_to_files(lease)

    uploads = request.FILES.getlist("files")
    if not uploads:
        messages.error(request, _("Pick at least one file to upload."))
        return _back_to_files(lease)
    folder = (request.POST.get("folder") or "").strip().strip("/")
    replace = bool(request.POST.get("replace"))

    done, refused = [], []
    for index, upload in enumerate(uploads):
        # The browser sends a bare name; a few old ones sent a Windows path.
        # This is the upload's own filename, cleaned before it becomes a name
        # in the area — the area's rule (no backslashes at all) is the
        # executor's, and it still applies to what comes out of here.
        leaf = os.path.basename((upload.name or "").replace("\\", "/"))
        label = leaf or _("a file with no name")
        if index >= MAX_FILES_PER_TRANSFER:
            refused.append((label, _("more than %(n)s files at once")
                            % {"n": MAX_FILES_PER_TRANSFER}))
            continue
        if not leaf:
            refused.append((label, _("it has no name")))
            continue
        if upload.size > MAX_UPLOAD_BYTES:
            # Refused on the size the browser declared, BEFORE reading: the
            # point of the limit is that these bytes never sit in this process.
            refused.append((leaf, _("over the %(mb)s MB upload limit")
                            % {"mb": MAX_UPLOAD_BYTES // (1024 * 1024)}))
            continue
        name = f"{folder}/{leaf}" if folder else leaf
        try:
            writer(lease, name, upload.read(), replace=replace)
        except (FilesRefused, RuntimeUnavailable) as exc:
            refused.append((name, _files_sentence(exc)))
        else:
            done.append(name)

    _report(request, done, refused, lambda names: _(
        "Uploaded into the Capsule: %(names)s.") % {"names": ", ".join(names)})
    return _back_to_files(lease)


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
    # A FOLDER is meaningful for any number: each file keeps its title and
    # lands under it — the folder a person pressed "Copy in" on.
    folder = (request.POST.get("folder") or "").strip().strip("/")

    done, refused = [], []
    for picked in ids:
        vault_file = wanted.get(picked)
        if vault_file is None:
            refused.append((picked, _("no such file")))
            continue
        target = name or (f"{folder}/{vault_file.title}" if folder else "")
        try:
            transfer.to_capsule(lease=lease, vault_file=vault_file, name=target,
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
