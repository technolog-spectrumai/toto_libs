from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from toto.ui import PageProcessor
from toto.vault.access import may_read
from toto.vault.models import VaultFile
from toto.quota import QuotaExceeded, check_quota, record_usage
from toto.quota.charge import InsufficientFunds, charge, check_funds, price_for

from .dispatch import create_service_run, dispatch_run, fail_run
from .models import FileserviceQuotaPolicy, FileserviceUsageEvent
from .models import FileServiceRun
from .plugin import FileServicePlugin


# Primary "Open tool" target per file type. Video/audio go to the manta builder
# when it is installed (BUILD_MANTA). Images have no file-service tool — the vault
# file list offers an inline image preview instead.
# A missing plugin simply yields a 404 from open_primary_service — no crash.
PRIMARY_SERVICE_BY_TYPE = {"video": "manta", "audio": "manta"}


@login_required
def services_for_file(request, file_pk):
    """JSON list of services applicable to a given vault file.

    Access-checked like the runner: this returns a file's TITLE, so an unchecked
    version is a way to read the name of anybody's file by walking primary keys.
    """
    vault_file = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory"), pk=file_pk)
    if not may_read(request.user, vault_file):
        raise Http404
    services = [p.to_dict() for p in FileServicePlugin.for_file(vault_file)]
    return JsonResponse({"services": services, "file_title": vault_file.title})


@login_required
def open_primary_service(request, file_pk):
    """Redirect straight to the primary tool for this file type (no modal)."""
    vault_file = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory"), pk=file_pk,
    )
    if not may_read(request.user, vault_file):
        raise Http404
    key = PRIMARY_SERVICE_BY_TYPE.get(vault_file.file_type)
    plugin = FileServicePlugin.get(key) if key else None
    if plugin and plugin.builder:
        url = plugin.builder_url(vault_file)
        if url:
            return redirect(url)
    raise Http404


@csrf_exempt
@login_required
def run_service(request, file_pk):
    """Create a FileServiceRun for the chosen service and dispatch it."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    vault_file = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory"), pk=file_pk,
    )

    # BEFORE the plugin is even resolved, and for every plugin — not only the
    # builder-backed ones. This check used to sit inside `if plugin.builder:`,
    # which meant ffmpeg, ffprobe and transcription skipped it entirely: a POST
    # naming somebody else's file pk staged their bytes into a temp dir, ran a
    # lossless remux over them, and filed the OUTPUT as a VaultFile owned by the
    # CALLER — a copy-anybody's-video primitive, with ffprobe and transcription
    # as the metadata and transcript variants.
    #
    # 404, not 403: a 403 confirms the file exists and turns pk-walking into an
    # enumeration oracle.
    if not may_read(request.user, vault_file):
        raise Http404

    service_key = request.POST.get("service_key", "").strip()
    args = request.POST.get("args", "")

    plugin = FileServicePlugin.get(service_key)
    if plugin is None or not plugin.accepts(vault_file):
        return JsonResponse({"error": "Service is not available for this file."}, status=400)

    # Builder-backed services collect arguments on a dedicated app page.
    if plugin.builder:
        url = plugin.builder_url(vault_file)
        if url:
            return JsonResponse({"status": "redirect", "redirect_url": url})

    if plugin.args_required and not args.strip():
        return JsonResponse({"error": f"{plugin.args_label} are required."}, status=400)

    tariff = price_for(request.user, "fileservices")
    try:
        check_quota(FileserviceQuotaPolicy, "fileservices.run", 1, request.user)
        check_funds(request.user, tariff, "fileservices.run", 1)
    except (QuotaExceeded, InsufficientFunds) as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status_code)

    run = create_service_run(request.user, vault_file, service_key, args)
    _src = {"source_type": "fileservices.FileServiceRun", "source_id": str(run.id)}
    if record_usage(FileserviceUsageEvent, "fileservices.run", 1, request.user,
                    idempotency_key=f"fileservices.run:{run.id}", **_src) is not None:
        charge(request.user, tariff, "fileservices.run", 1, **_src)
    run_url = reverse("fileservices:run_detail", args=[run.id])
    try:
        queued = dispatch_run(run)
    except Exception as exc:
        # Close the row too — before this, a failed dispatch left it PENDING
        # forever, which is exactly the shape the stuck-run sweeper exists to
        # catch; the request that watched it happen should not need a sweeper.
        # The INLINE path records its own failure (real stderr + refund) on a
        # fresh instance before re-raising — refresh so we don't clobber it
        # with a false "could not be queued".
        run.refresh_from_db()
        if run.status not in (FileServiceRun.SUCCESS, FileServiceRun.FAILED):
            fail_run(run, f"Could not be queued: {exc}")
        return JsonResponse({"status": "failed", "run_id": run.id, "run_url": run_url,
                             "error": str(exc)}, status=200)
    if queued:
        return JsonResponse({"status": "queued", "run_id": run.id, "run_url": run_url})
    return JsonResponse({"status": "ok", "run_id": run.id, "run_url": run_url, "ran_inline": True})


def _run_payload(run: FileServiceRun) -> dict:
    return {
        "id": run.id,
        "service_key": run.service_key,
        "status": run.status,
        "stdout": run.stdout,
        "stderr": run.stderr,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "outputs": [
            {"pk": f.pk, "title": f.title, "file_type": f.file_type,
             "url": f.get_public_url() or ""}
            for f in run.output_files
        ],
    }


@login_required
def run_status(request, run_id):
    run = get_object_or_404(FileServiceRun, pk=run_id, owner=request.user)
    return JsonResponse(_run_payload(run))


class RunListView(LoginRequiredMixin, View):
    """The section landing page: your own runs, newest first.

    Until 1.21 fileservices had ``run_detail`` but no index — the only way to a
    run was the redirect you got right after starting one, so a run you navigated
    away from was unreachable. The Media sub-nav needs somewhere to point, and
    this is it.

    Owner-scoped, deliberately: a run's stdout/stderr can quote file contents.
    """

    template_name = "fileservices/run_list.html"
    login_url = reverse_lazy("core:login")

    # Bound the page. Runs accumulate one row per ffmpeg invocation and there is
    # no pagination here yet; the newest RUN_LIST_CAP are what anyone acts on.
    RUN_LIST_CAP = 200

    def get(self, request):
        runs = list(
            FileServiceRun.objects
            .filter(owner=request.user)
            .select_related("input_file", "bucket")
            .order_by("-started_at")[: self.RUN_LIST_CAP]
        )

        rows = []
        for run in runs:
            plugin = FileServicePlugin.get(run.service_key)
            rows.append({
                "id": run.id,
                "title": plugin.get_title() if plugin else run.service_key,
                "icon": plugin.icon if plugin else "fa-solid fa-wand-magic-sparkles",
                "status": run.status,
                "args": run.args,
                "input_title": run.input_file.title if run.input_file else "—",
                "bucket": run.bucket.name if run.bucket else "—",
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "output_count": len(run.output_file_pks or []),
                "url": reverse("fileservices:run_detail", args=[run.id]),
            })

        from toto.quota import times

        context = PageProcessor().decorate(
            {
                "rows": rows,
                "capped": len(runs) >= self.RUN_LIST_CAP,
                "run_list_cap": self.RUN_LIST_CAP,
                # The user's runtime dial; set_url is "" without the levy
                # engine and the template hides the card on that.
                "time_dial": times.dial("fileservices.run_runtime",
                                        user=request.user),
            },
            request,
        )
        return render(request, self.template_name, context)


class RunDetailView(LoginRequiredMixin, View):
    template_name = "fileservices/run_detail.html"
    login_url = reverse_lazy("core:login")

    def get(self, request, run_id):
        run = get_object_or_404(
            FileServiceRun.objects.select_related("input_file", "bucket", "workflow_run"),
            pk=run_id, owner=request.user,
        )
        plugin = FileServicePlugin.get(run.service_key)
        context = PageProcessor().decorate(
            {
                "run": run,
                "service_title": plugin.get_title() if plugin else run.service_key,
                "service_icon": plugin.icon if plugin else "fa-solid fa-wand-magic-sparkles",
                "status_url": reverse("fileservices:run_status", args=[run.id]),
                "outputs": run.output_files,
            },
            request,
        )
        return render(request, self.template_name, context)
