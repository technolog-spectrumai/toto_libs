"""Text recognition: submit a scan, watch it, take the text.

An Office application. Until 1.51 this ran Tesseract inside an Anastasia
Compute Gear, synchronously, from the POST handler — and before that it ran it
inline with no size cap, no type check and no meter. It now runs on this host,
on a worker, one Celery task per page.

**This app keeps its own URL namespace, and that is load-bearing.**
`toto.subscriptions.gate` reads the entitlement from `resolver_match.app_name`;
Office's namespace is deliberately free and GET-only, because (its own words)
"an Office-owned write route would be a way to create paid content for nothing".
A POST-accepting page mounted under /office/ would be exactly that bypass. So
Office LINKS here, and the writes stay under `ocr:`.

Permissions, at a glance: anyone signed in may read their own scans; a run is
readable by its owner and by staff and NOT FOUND to anybody else — 404 rather
than 403, because the existence of somebody's document is not ours to confirm.
Downloading the text is a GET on purpose, so a lapsed plan never traps work that
was already paid for behind a paywall.
"""

from __future__ import annotations

import os

from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import IntegrityError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

ENTITLEMENT = "ocr"
METRIC = "ocr.page"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _buckets_and_directories(user):
    """The user's own buckets plus their directories (path label, per bucket).

    Returns ``(buckets, directories)`` where directories is a flat list of
    ``{"pk", "bucket_pk", "label"}`` the template filters by selected bucket.
    Folder paths are built in Python (2 queries) instead of per-row walks.
    """
    from toto.vault.models import Bucket, VaultDirectory

    buckets = list(Bucket.objects.filter(owner=user).order_by("name")
                   .values("pk", "name"))
    nodes = {
        d["pk"]: d
        for d in VaultDirectory.objects.filter(bucket__owner=user).values(
            "pk", "name", "parent_id", "bucket_id")
    }

    def _path(pk):
        parts, seen = [], set()
        node = nodes.get(pk)
        while node and node["pk"] not in seen:
            seen.add(node["pk"])
            parts.append(node["name"])
            node = nodes.get(node["parent_id"])
        return "/".join(reversed(parts))

    directories = [
        {"pk": d["pk"], "bucket_pk": d["bucket_id"], "label": _path(d["pk"])}
        for d in nodes.values()
    ]
    directories.sort(key=lambda x: (x["bucket_pk"], x["label"]))
    return buckets, directories


def _unique_key(bucket, base_key):
    from toto.vault.models import VaultFile

    base_key = base_key or "text"
    key, i = base_key, 1
    while VaultFile.objects.filter(bucket=bucket, key=key).exists():
        key = f"{base_key}-{i}"
        i += 1
    return key


def _own_run(request, pk):
    """A run this person may see, or 404.

    Owner or staff. Not 403: a refusal that distinguishes "not yours" from
    "does not exist" tells a stranger that it exists.
    """
    from toto.ocr.models import OcrRun

    run = get_object_or_404(OcrRun, pk=pk)
    user = request.user
    if run.owner_id != user.id and not user.is_staff:
        raise Http404("Not found.")
    return run


def _settings():
    from toto.ocr.models import OcrSettings

    return OcrSettings.get()


# ---------------------------------------------------------------------------
# The tool page
# ---------------------------------------------------------------------------

@login_required
def ocr_home(request):
    """Upload something and read it — or pick up where a scan got to."""
    from toto.core import office
    from toto.ocr import engine
    from toto.ocr.models import OcrRun

    limits = _settings()
    vault_file = None
    file_pk = (request.GET.get("file") or "").strip()
    if file_pk.isdigit():
        # Arrived from the vault's wand. Reading the text out of a file you may
        # already read is not a new right, so `may_read` is the whole check.
        from toto.vault import access
        from toto.vault.models import VaultFile

        candidate = VaultFile.objects.filter(pk=int(file_pk)).first()
        if candidate is not None and access.may_read(request.user, candidate):
            vault_file = candidate

    context = {
        "tesseract_available": engine.tesseract_available(),
        "pdf_available": engine.pdf_support_available(),
        "languages": engine.language_choices(),
        "default_language": (engine.available_languages() or ["eng"])[0],
        "max_upload_mb": limits.max_upload_mb,
        "max_pages": limits.max_pages_per_run,
        "retention_days": limits.retention_days,
        "vault_file": vault_file,
        "recent": OcrRun.objects.filter(owner=request.user)[:10],
        # The TOOLS tab strip, with this page's own tab lit. Reading a scan
        # was an Office tab until 2026-09-01 and this asked for `office_tabs`;
        # tools have a room of their own now, and the strip moved with them.
        #
        # Still asked rather than hardcoded, for the same reason: a host that
        # mounts no tools renders an empty strip instead of a row of dead
        # links, and a host that adds one gets it here for free. Arriving here
        # must not drop you out of the set you picked this from.
        "sections": office.tools_tabs(active="ocr"),
    }
    return render(request, "ocr/ocr.html",
                  PageProcessor().decorate(context, request))


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------

@require_POST
@login_required
def ocr_submit(request):
    """Accept a file, freeze the page list, and queue a task per page."""
    from toto.ocr import dispatch, engine, runs, validation
    from toto.ocr.models import OcrQuotaPolicy, OcrRun, RunStatus
    from toto.quota import InArrears, QuotaExceeded, check_quota
    from toto.quota.charge import InsufficientFunds, check_funds, price_for

    if not engine.tesseract_available():
        return JsonResponse({"error": _(
            "Text recognition is not installed on this server.")}, status=503)

    language = (request.POST.get("language") or "").strip() or "eng"
    if not engine.is_offered(language):
        return JsonResponse({"error": _(
            "This server does not have that language. It has: %(langs)s.")
            % {"langs": ", ".join(engine.available_languages())}}, status=400)

    limits = _settings()
    max_bytes = limits.max_upload_mb * 1024 * 1024
    page_cap = limits.max_pages_per_run

    group = request.FILES.getlist("document")
    uploaded = group[0] if len(group) == 1 else None
    vault_file = None
    if len(group) > 1:
        # Several images, one page each, in the order they were chosen.
        inspection = validation.inspect_group(
            group, max_bytes=max_bytes, page_cap=page_cap)
        source_name = _("%(first)s and %(n)s more") % {
            "first": group[0].name or "scan", "n": len(group) - 1}
    elif uploaded is not None:
        group = None
        inspection = validation.inspect_upload(
            uploaded, max_bytes=max_bytes, page_cap=page_cap)
        source_name = uploaded.name or "scan"
    else:
        group = None
        from toto.vault import access
        from toto.vault.models import VaultFile

        file_pk = (request.POST.get("file") or "").strip()
        if not file_pk.isdigit():
            return JsonResponse({"error": _("Choose a file to read.")},
                                status=400)
        vault_file = VaultFile.objects.filter(pk=int(file_pk)).first()
        if vault_file is None or not access.may_read(request.user, vault_file):
            raise Http404("Not found.")
        if vault_file.is_encrypted:
            return JsonResponse({"error": _(
                "That file is encrypted. Decrypt it first.")}, status=400)
        inspection = validation.inspect_vault_file(
            vault_file, max_bytes=max_bytes, page_cap=page_cap)
        source_name = vault_file.title or "scan"

    if not inspection.ok:
        status = 413 if inspection.reason == "too-large" else 400
        return JsonResponse({"error": inspection.message,
                             "reason": inspection.reason}, status=status)

    # One job at a time, per person. The single most effective control there
    # is: this platform runs every background job on ONE queue, so without it
    # one person with a shelf of books delays the nightly levies, the forum
    # cleanup and everybody else's transfers. The vault's transfers refuse a
    # second run the same way.
    if OcrRun.objects.filter(owner=request.user,
                             status__in=[RunStatus.PENDING,
                                         RunStatus.RUNNING]).exists():
        return JsonResponse({"error": _(
            "One of your scans is still being read. Wait for it to finish.")},
            status=409)

    # Money and plan, ONCE, for the whole job, before any work or any bytes are
    # stored. Refusing here costs nobody anything; refusing halfway would.
    try:
        check_quota(OcrQuotaPolicy, METRIC, inspection.page_count, request.user)
        check_funds(request.user, price_for(request.user, ENTITLEMENT),
                    METRIC, inspection.page_count)
    except (QuotaExceeded, InArrears, InsufficientFunds) as exc:
        return JsonResponse({"error": str(exc)},
                            status=getattr(exc, "status_code", 402))

    run = runs.create_run(owner=request.user, inspection=inspection,
                          language=language, source_name=source_name,
                          uploaded=uploaded, vault_file=vault_file,
                          group=group)
    try:
        dispatch.dispatch_run(run)
    except dispatch.CannotQueue as exc:
        runs.fail_run(run, str(exc))
        return JsonResponse({"error": str(exc), "run_id": run.pk}, status=503)

    return JsonResponse({"status": "ok", "run_id": run.pk,
                         "url": f"/ocr/run/{run.pk}/",
                         "total_pages": run.total_pages})


# ---------------------------------------------------------------------------
# Watching, and the result
# ---------------------------------------------------------------------------

@login_required
def ocr_run_detail(request, pk):
    from toto.ocr import runs

    run = _own_run(request, pk)
    payload = runs.run_payload(run)
    buckets, directories = _buckets_and_directories(request.user)
    context = {
        "run": run,
        "payload": payload,
        "buckets": buckets,
        "directories": directories,
        "can_retry": bool(run.is_finished and run.retry_page_numbers()
                          and (run.source or run.source_file)),
        "retention_days": _settings().retention_days,
    }
    return render(request, "ocr/run_detail.html",
                  PageProcessor().decorate(context, request))


@login_required
def ocr_status(request, pk):
    """The poll endpoint. One row read plus one grouped count."""
    from toto.ocr import runs

    return JsonResponse(runs.run_payload(_own_run(request, pk)))


@login_required
def ocr_text(request, pk):
    """The plain text, as a download.

    A GET, deliberately: work already done must never become unreachable
    because a subscription lapsed.
    """
    run = _own_run(request, pk)
    stem = slugify(os.path.splitext(run.source_name or "scan")[0]) or "scan"
    response = HttpResponse(run.text or "", content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{stem}.txt"'
    return response


# ---------------------------------------------------------------------------
# Acting on a finished run
# ---------------------------------------------------------------------------

@require_POST
@login_required
def ocr_save(request, pk):
    """Write the text into the vault as an ordinary .txt document.

    Nothing is saved unless asked. This route lives here rather than in Office
    because Office owns no writes, and the vault's own create door seeds an
    EMPTY file from an editor plugin — it cannot accept bytes.
    """
    from toto.vault.models import Bucket, VaultDirectory, VaultFile

    run = _own_run(request, pk)
    if not run.text:
        return JsonResponse({"error": _("There is no text to save yet.")},
                            status=400)

    bucket = Bucket.objects.filter(pk=request.POST.get("bucket"),
                                   owner=request.user).first()
    if bucket is None:
        return JsonResponse({"error": _("Pick a folder you own to save to.")},
                            status=400)
    directory = None
    dir_pk = request.POST.get("directory")
    if dir_pk:
        directory = VaultDirectory.objects.filter(pk=dir_pk, bucket=bucket).first()
        if directory is None:
            return JsonResponse(
                {"error": _("That folder is not in the selected place.")},
                status=400)

    stem = os.path.splitext(run.source_name or "scan")[0] or "scan"
    title = f"{stem}.txt"
    data = run.text.encode("utf-8")
    vault_file = VaultFile(
        owner=request.user, title=title,
        key=_unique_key(bucket, slugify(stem) or "text"),
        bucket=bucket, directory=directory, file_type="text", is_public=False)
    try:
        vault_file.file.save(title, ContentFile(data), save=True)
    except IntegrityError:
        # Lost a race on the (bucket, key) unique constraint — the SELECT in
        # _unique_key and this INSERT aren't atomic. Surface a clean error.
        return JsonResponse({"error": _(
            "A file with that name was just saved here — try again.")},
            status=409)
    try:
        vault_file.content_hash = vault_file.create_hash()
        vault_file.file_size_bytes = len(data)
        vault_file.save(update_fields=["content_hash", "file_size_bytes"])
    except Exception:  # noqa: BLE001 — hashing is best-effort
        pass
    return JsonResponse({"status": "ok", "file_pk": vault_file.pk,
                         "title": title})


@require_POST
@login_required
def ocr_cancel(request, pk):
    """Stop what has not started; keep what is already read."""
    from toto.ocr import dispatch, runs

    run = _own_run(request, pk)
    if run.is_finished:
        return JsonResponse({"error": _("That scan has already finished.")},
                            status=409)
    stopped = dispatch.cancel_run(run)
    run.refresh_from_db()
    return JsonResponse({"status": "ok", "stopped": stopped,
                         **runs.run_payload(run)})


@require_POST
@login_required
def ocr_retry(request, pk):
    """Read the pages that did not deliver, on the same run.

    In place rather than as a new row, which is where this differs from the
    vault's transfers — and the difference is principled. A transfer mints a new
    run because its counters record bytes that actually moved and money that was
    actually spent, and mutating them would rewrite that record. Here a page is
    charged only when it delivers, so a page that never delivered has no record
    to protect.
    """
    from toto.ocr import dispatch, runs
    from toto.ocr.models import OcrPage, OcrRun, PageStatus, RunStatus
    from toto.ocr.times import page_budget

    run = _own_run(request, pk)
    numbers = run.retry_page_numbers()
    if not numbers:
        return JsonResponse({"error": _("Every page was read.")}, status=400)
    try:
        runs.source_path(run)
    except Exception:  # noqa: BLE001
        return JsonResponse({"error": _(
            "The original file has been removed, so these pages cannot be "
            "read again. Scans are kept for %(days)s days.")
            % {"days": _settings().retention_days}}, status=409)
    if OcrRun.objects.filter(owner=request.user,
                             status__in=[RunStatus.PENDING, RunStatus.RUNNING]
                             ).exclude(pk=run.pk).exists():
        return JsonResponse({"error": _(
            "Another of your scans is running. Wait for it to finish.")},
            status=409)

    OcrPage.objects.filter(run=run, number__in=numbers).update(
        status=PageStatus.WAITING, error="")
    # F(), not arithmetic on the row we read: the same race the cancel path
    # has. Nothing else is settling this run right now — it is finished — but
    # the habit is what keeps the two paths from drifting apart.
    from django.db.models import F

    OcrRun.objects.filter(pk=run.pk).update(
        status=RunStatus.RUNNING, finished_at=None, error="",
        page_errors=[],
        pages_settled=F("pages_settled") - len(numbers),
        pages_failed=F("pages_failed") - len(numbers))

    from toto.ocr.tasks import ocr_page

    budget = page_budget(request.user)
    for number in numbers:
        result = ocr_page.apply_async(args=[run.pk, number],
                                      soft_time_limit=budget,
                                      time_limit=budget + 60)
        OcrPage.objects.filter(run=run, number=number).update(task_id=result.id)
    return JsonResponse({"status": "ok", "retried": len(numbers)})
