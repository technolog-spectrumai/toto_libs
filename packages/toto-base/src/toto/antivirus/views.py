"""Three tabs: the files, the numbers, and the findings.

**Files** is the vault's own tree in a security-focused, read-only form — no
upload, no management, just the health of every file you can read and a Scan
action on the ones that are yours to scan. **Statistics** is the aggregate
picture. **Pathology** is every finding, current or historic, with enough
detail to investigate.

Scope rule, everywhere: a regular user sees their own files; staff see the
whole platform, and the page says which view it is. The SCANNABLE set stays
exactly ``_my_files`` — the same queryset ``scan_file`` accepts, so an enabled
control cannot point at an endpoint that will refuse it.
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor
from toto.vault.filetree import accessible_files, build_file_tree
from toto.vault.scanning import SCANNABLE_TYPES

from . import dispatch, engine, services
from .models import (ScanPreference, ScanResult, ScanRun, ScanVerdict,
                     severity_of)


def _render(request, template_name, context):
    return render(request, template_name,
                  PageProcessor().decorate(context, request))


def _is_operator(user) -> bool:
    """``is_superuser`` does not imply ``is_staff`` in Django. Both count."""
    return bool(user.is_staff or user.is_superuser)


def _my_files(user):
    """Files this person may scan — and the ONE definition of that.

    `accessible_files` minus its public arm: a stranger's public file is
    readable, but it is not this person's to screen, and letting anyone queue
    work against anyone's files is how a scan button becomes an amplifier.

    Encrypted files are dropped here too, because ``scan_file`` refuses them —
    the tree may LIST more than this (disabled), but every enabled control
    resolves to a file this queryset contains.

    There is deliberately no owner filter beyond the claim arms above:
    ``VaultFile.owner`` is NOT NULL, so the ``.exclude(owner__isnull=True)``
    this used to carry could never match a row — dead code wearing a security
    comment, which is worse than no code.
    """
    return (accessible_files(user, file_types=list(SCANNABLE_TYPES),
                             include_public=False)
            .filter(is_encrypted=False))


def _scoped_results(user):
    """Scan results this person may read about: their files, or all for staff."""
    if _is_operator(user):
        return ScanResult.objects.all(), True
    return (ScanResult.objects.filter(
        file_id__in=_my_files(user).values("pk")), False)


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def _current_verdicts(meta: dict) -> dict:
    """file_id → verdict, for the bytes each file holds NOW.

    ``meta`` maps id → its current content hash. A verdict about older bytes is
    history and stays out of this map — the tree's icons are about the files as
    they are, and Pathology is where the past lives.
    """
    verdicts = {}
    for row in (ScanResult.objects.filter(file_id__in=meta)
                .values("file_id", "content_sha256", "verdict")):
        if row["content_sha256"] == meta.get(row["file_id"]):
            verdicts[row["file_id"]] = row["verdict"]
    return verdicts


def _annotate_tree(tree, meta, scannable_ids, verdicts) -> None:
    """Walk the tree's row dicts and stamp each with its security state.

    Done here rather than in ``filetree._row`` because the vault's tree is
    shared and knows nothing about scanning — the rows are plain dicts, and
    mutating them is exactly what they are for.

    ``status``: clean | threat | failed | unscanned (scannable rows only).
    ``reason``: why a row is disabled (unscannable rows only).
    """
    for bnode in tree:
        for group in bnode["groups"]:
            for row in group["files"]:
                m = meta.get(row["id"], {})
                if row["id"] in scannable_ids:
                    row["scannable"] = True
                    verdict = verdicts.get(row["id"])
                    if verdict == ScanVerdict.CLEAN:
                        row["status"] = "clean"
                    elif verdict == ScanVerdict.REFUSED:
                        row["status"] = "threat"
                    elif verdict == ScanVerdict.ERROR:
                        row["status"] = "failed"
                    else:
                        row["status"] = "unscanned"
                else:
                    row["scannable"] = False
                    if m.get("is_encrypted"):
                        row["reason"] = _("Encrypted — the scanner cannot read it.")
                    elif m.get("file_type") not in SCANNABLE_TYPES:
                        row["reason"] = _("This type cannot be scanned.")
                    else:
                        row["reason"] = _("Not yours to scan.")


@login_required
def index(request):
    """The vault's tree, read-only, with the health of every row.

    Lists everything the user may READ — more than they may scan, with the
    difference visible: a scannable row carries its status icon and a Scan
    action, an unscannable one is disabled with the reason. No upload, no
    management — that is the vault's job, and this page's job is security.
    """
    readable = accessible_files(request.user)
    meta = {v["pk"]: v for v in readable.values(
        "pk", "is_encrypted", "file_type", "owner_id", "content_hash")}
    scannable_ids = set(_my_files(request.user).values_list("pk", flat=True))

    hash_by_id = {pk: (m["content_hash"] or "unreadable")
                  for pk, m in meta.items() if pk in scannable_ids}
    verdicts = _current_verdicts(hash_by_id)

    tree = build_file_tree(request.user, queryset=readable)
    _annotate_tree(tree, meta, scannable_ids, verdicts)

    results, _staff = _scoped_results(request.user)
    threats = results.filter(verdict=ScanVerdict.REFUSED)

    return _render(request, "antivirus/index.html", {
        "active_tab": "files",
        "threats_found": threats.count(),
        "files_scanned": results.values("file_id").distinct().count(),
        "tree": tree,
        "scannable_types": SCANNABLE_TYPES,
        # JSON, not the tuple: Alpine has to parse it, and a Python
        # repr with single quotes is not JavaScript.
        "auto_types_json": json.dumps(list(ScanPreference.types_for(request.user))),
    })


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

#: The chart's verdict colours: clean green, refused red, error amber.
_VERDICT_SERIES = [
    (ScanVerdict.CLEAN, "Clean", "#10B981"),
    (ScanVerdict.REFUSED, "Threats", "#EF4444"),
    (ScanVerdict.ERROR, "Failed", "#F59E0B"),
]


@login_required
def statistics(request):
    """The aggregate picture: how much has been scanned, and what came of it."""
    results, staff_view = _scoped_results(request.user)

    totals = results.aggregate(scans=Count("id"), data=Sum("size_bytes"))
    # order_by() on the aggregate is load-bearing: ScanResult.Meta orders by
    # -scanned_at, and Django folds an ORDER BY column into the GROUP BY —
    # which would return one row per scan rather than one per verdict.
    by_verdict = dict(results.values_list("verdict")
                      .annotate(n=Count("id")).order_by())

    return _render(request, "antivirus/statistics.html", {
        "active_tab": "statistics",
        "staff_view": staff_view,
        "files_scanned": results.values("file_id").distinct().count(),
        "scans_run": totals["scans"] or 0,
        "data_scanned": totals["data"] or 0,
        "clean_count": by_verdict.get(ScanVerdict.CLEAN, 0),
        "threat_count": by_verdict.get(ScanVerdict.REFUSED, 0),
        "failed_count": by_verdict.get(ScanVerdict.ERROR, 0),
        "activity_chart_json": _activity_chart_json(results),
    })


def _activity_chart_json(results, *, days=30) -> str:
    """Scans per day for the last 30 days, stacked by verdict. "" when empty.

    Empty days are padded to zero so the axis is a real calendar — three active
    days side by side read as three consecutive days otherwise. The trailing
    ``order_by("day")`` defeats the Meta-ordering GROUP BY trap.
    """
    today = timezone.localdate()
    since = today - timedelta(days=days - 1)

    counted: dict = {}
    rows = (results.filter(scanned_at__date__gte=since)
            .annotate(day=TruncDate("scanned_at"))
            .values("day", "verdict")
            .annotate(n=Count("id"))
            .order_by("day"))
    for row in rows:
        counted.setdefault(row["verdict"], {})[row["day"]] = row["n"]

    if not counted:
        return ""

    labels = [(since + timedelta(days=i)).strftime("%m-%d") for i in range(days)]
    datasets = []
    for verdict, label, colour in _VERDICT_SERIES:
        by_day = counted.get(verdict)
        if not by_day:
            continue
        datasets.append({
            "label": label,
            "data": [by_day.get(since + timedelta(days=i), 0)
                     for i in range(days)],
            "backgroundColor": colour,
        })

    return json.dumps({
        "chart_type": "bar",
        "labels": labels,
        "datasets": datasets,
        "options": {"scales": {"x": {"stacked": True},
                               "y": {"stacked": True, "beginAtZero": True}}},
    })


# ---------------------------------------------------------------------------
# Pathology
# ---------------------------------------------------------------------------

#: The most findings one page shows. A diagnostic surface, not an archive —
#: the same cap idea as jess's outbox.
FINDINGS_CAP = 200


@login_required
def pathology(request):
    """Every finding: what was detected, where, when, and whether it is still there.

    REFUSED and ERROR both belong here — a file that cannot be checked is a
    security fact of its own kind. Each row says whether the finding is about
    the bytes the file holds NOW or about a version somebody has since
    replaced: a fixed file must not go on reading as infected.
    """
    results, staff_view = _scoped_results(request.user)
    findings = (results.filter(
        verdict__in=[ScanVerdict.REFUSED, ScanVerdict.ERROR])
        .select_related("file", "file__bucket", "scanned_by")[:FINDINGS_CAP])

    rows = []
    for finding in findings:
        vault_file = finding.file
        current_hash = (vault_file.content_hash or "unreadable")
        rows.append({
            "finding": finding,
            "severity": severity_of(finding.verdict, finding.reason),
            "current": finding.content_sha256 == current_hash,
            "url": _file_url(vault_file),
        })

    return _render(request, "antivirus/pathology.html", {
        "active_tab": "pathology",
        "staff_view": staff_view,
        "rows": rows,
        "capped": len(rows) == FINDINGS_CAP,
        "findings_cap": FINDINGS_CAP,
    })


def _file_url(vault_file) -> str:
    """A way to open the file, or "". The download route honours ``may_read``,
    so an owner reaches their own private file and a stranger gets a 404."""
    if not vault_file.bucket_id or not vault_file.key:
        return ""
    try:
        return reverse("vault:public_file",
                       args=[vault_file.bucket.slug, vault_file.key])
    except NoReverseMatch:
        return ""


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@login_required
def settings_view(request):
    """Your automatic-scan preference, and — for staff — the scanner's tuning.

    Two different kinds of setting on one tab, gated differently on purpose:
    the preference narrows YOUR OWN files and belongs to everybody; the
    scanner parameters are platform security policy and belong to staff.
    """
    from .models import DOOR_GROUPS, ScannerConfig
    from .scanners.config import DEFAULTS, params

    preference = ScanPreference.objects.filter(user=request.user).first()
    doors_chosen = (preference.doors if preference and preference.doors is not None
                    else list(DOOR_GROUPS))

    context = {
        "active_tab": "settings",
        "scannable_types": SCANNABLE_TYPES,
        "types_json": json.dumps(list(ScanPreference.types_for(request.user))),
        "door_groups": [
            ("editor", _("Editor saves"), _("Files written from the editors, over HTTP or the live socket.")),
            ("upload", _("Uploads"), _("Files arriving through the vault API and the gateway pages.")),
            ("restore", _("Version restores"), _("Old versions brought back — a rule added since may refuse what was once accepted.")),
        ],
        "doors_json": json.dumps(doors_chosen),
        "is_operator": _is_operator(request.user),
    }
    if _is_operator(request.user):
        context.update({
            "scanner_params": params(),
            "scanner_defaults": DEFAULTS,
            "scanner_overrides": ScannerConfig.get().params or {},
        })
    return _render(request, "antivirus/settings.html", context)


@login_required
@require_POST
def set_scanner_config(request):
    """Store the scanner overrides. Staff only — this is security policy."""
    from .models import ScannerConfig
    from .scanners.config import DEFAULTS

    if not _is_operator(request.user):
        return JsonResponse({"ok": False, "error": "Staff only."}, status=403)

    overrides = {}
    for key, default in DEFAULTS.items():
        if key not in request.POST:
            continue
        raw = request.POST[key]
        if isinstance(default, bool):
            value = raw in ("1", "true", "on", "True")
        else:
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return JsonResponse(
                    {"ok": False, "error": f"{key} must be a number."},
                    status=400)
            if value <= 0:
                return JsonResponse(
                    {"ok": False, "error": f"{key} must be positive."},
                    status=400)
        # Only real OVERRIDES are stored; a value matching the default stays
        # out of the row, so tightening a default later is not silently pinned
        # by everyone who once pressed Save.
        if value != default:
            overrides[key] = value

    config = ScannerConfig.get()
    config.params = overrides
    config.updated_by = request.user
    config.save(update_fields=["params", "updated_by", "updated_at"])
    return JsonResponse({"ok": True, "overrides": overrides})


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

@login_required
@require_POST
def set_preference(request):
    """Which types, at which doors, get screened for this person's own files."""
    from .models import DOOR_GROUPS

    chosen = [t for t in request.POST.getlist("types") if t in SCANNABLE_TYPES]
    doors = [d for d in request.POST.getlist("doors") if d in DOOR_GROUPS]
    ScanPreference.objects.update_or_create(
        user=request.user, defaults={"types": chosen, "doors": doors})
    return JsonResponse({"ok": True, "types": chosen, "doors": doors})


@login_required
@require_POST
def scan_file(request, pk):
    """Queue one scan, on purpose. Returns ``{run_id}`` or a reason it will not.

    Always allowed whatever the automatic preferences say — pressing the button
    is the deliberate act the preferences exist to be an alternative to. The
    scan itself runs on a worker: it is BILLED work with a run row, and the
    browser polls ``scan_status`` exactly as it polls texlab and steven.

    Affordability is checked BEFORE anything is queued — nobody occupies a
    worker they cannot pay for — and the charge lands after the verdict, in
    ``services.execute``.
    """
    vault_file = get_object_or_404(_my_files(request.user), pk=pk)

    try:
        services.check_affordable(request.user)
    except Exception as exc:  # noqa: BLE001 - QuotaExceeded / InsufficientFunds
        status = getattr(exc, "status_code", None)
        if status is None:
            raise
        return JsonResponse({"ok": False, "error": str(exc)}, status=status)

    run = dispatch.create_run(user=request.user, vault_file=vault_file)
    try:
        dispatch.dispatch_run(run)
    except dispatch.CannotQueue:
        # No worker (a bare runserver, celery down). A scan is a bounded pass
        # over bounded bytes — the fileservices precedent, not the steven
        # case — so it runs inline rather than telling the user scanning is
        # broken. Same run row, same billing, same verdict path; the worker
        # is used whenever one is listening.
        services.execute(run)

    payload = {
        "ok": True,
        "run_id": run.pk,
        "status": run.status,
        "status_url": reverse("antivirus:scan_status", args=[run.pk]),
    }
    if run.is_finished:
        # The inline path already has the verdict; handing it back saves the
        # client a poll against a run that will never change again.
        payload.update(services.run_payload(run))
    return JsonResponse(payload)


@login_required
def scan_status(request, pk):
    """Poll one scan run. Owner only."""
    run = get_object_or_404(ScanRun.objects.select_related("result", "file"),
                            pk=pk, owner=request.user)
    return JsonResponse(services.run_payload(run))
