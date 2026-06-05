from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from toto.fileservices.access import user_can_access_vault_file
from toto.fileservices.dispatch import create_service_run, dispatch_run
from toto.ui import PageProcessor
from toto.vault.models import VaultFile


@login_required
def home(request):
    """Landing page: pick one or more image files from a tree and OCR them."""
    from toto.vault.filetree import build_file_tree

    if request.method == "POST":
        lang = request.POST.get("language", "").strip()
        files = [
            f for f in VaultFile.objects.filter(pk__in=request.POST.getlist("file_pks"))
            .select_related("bucket", "directory")
            if f.file_type == "image" and user_can_access_vault_file(request.user, f)
        ]
        if not files:
            messages.error(request, "Select at least one image file.")
            return redirect("ocr:home")
        runs = []
        for f in files:
            run = create_service_run(request.user, f, "ocr", lang)
            try:
                dispatch_run(run)
            except Exception as exc:
                messages.error(request, f"{f.title}: {exc}")
            runs.append(run)
        messages.success(request, f"Started OCR for {len(runs)} file(s).")
        if len(runs) == 1:
            return redirect("fileservices:run_detail", run_id=runs[0].id)
        return redirect("ocr:home")

    tree = build_file_tree(request.user, file_types=["image"])
    context = PageProcessor().decorate({"tree": tree}, request)
    return render(request, "ocr/home.html", context)


@login_required
def run_page(request, file_pk):
    """Collect OCR options for an image and run it.

    Builder-style entry point reached by redirect from the vault service menu.
    Access is verified independently here.
    """
    vf = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner"), pk=file_pk
    )
    if not user_can_access_vault_file(request.user, vf):
        raise Http404
    if vf.file_type != "image":
        raise Http404  # OCR is image-only

    if request.method == "POST":
        lang = request.POST.get("language", "").strip()
        run = create_service_run(request.user, vf, "ocr", lang)
        try:
            dispatch_run(run)
        except Exception as exc:
            messages.error(request, f"OCR failed: {exc}")
        else:
            messages.success(request, "OCR started.")
        return redirect("fileservices:run_detail", run_id=run.id)

    context = PageProcessor().decorate({"vf": vf}, request)
    return render(request, "ocr/run_page.html", context)
