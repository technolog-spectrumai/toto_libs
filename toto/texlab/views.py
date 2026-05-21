import uuid

from django.core.files.base import ContentFile
from django.views.generic import ListView
from toto.ui import PageProcessor
from django.views.generic import DetailView
from django.shortcuts import get_object_or_404
from toto.ui import PageProcessor
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from toto.celery_utils import celery_available
from toto.texlab.tasks import compile_latex_task
from toto.texlab.models import CompileRun, LatexFile
from toto.vault.models import VaultFile, Bucket
from toto.texlab.compile import compile_tex_to_pdf
from django.utils.text import slugify
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy, reverse
from toto.texlab.models import LatexWorkspace
from toto.vault.models import Bucket
from django.contrib.auth.decorators import login_required


class WorkspaceListView(LoginRequiredMixin, ListView):
    model = LatexWorkspace
    template_name = "texlab/workspace_list.html"
    context_object_name = "workspaces"
    login_url = reverse_lazy("core:login")

    def get_queryset(self):
        # Workspaces belong to buckets → buckets belong to owners
        return LatexWorkspace.objects.filter(
            bucket__owner=self.request.user
        ).order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


class WorkspaceDetailView(LoginRequiredMixin, DetailView):
    model = LatexWorkspace
    template_name = "texlab/workspace_detail.html"
    context_object_name = "workspace"
    slug_field = "slug"
    slug_url_kwarg = "slug"
    login_url = reverse_lazy("core:login")

    def get_object(self, queryset=None):
        # Validate workspace belongs to the logged-in user
        return get_object_or_404(
            LatexWorkspace,
            slug=self.kwargs["slug"],
            bucket__owner=self.request.user
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        workspace = self.get_object()

        # Files no longer have filename → sort by vault_file.title
        context["files"] = workspace.files.select_related("vault_file").order_by(
            "vault_file__title"
        )

        return PageProcessor().decorate(context, self.request)


class FileDisplayView(LoginRequiredMixin, DetailView):
    model = LatexFile
    template_name = "texlab/file_display.html"
    context_object_name = "file"
    pk_url_kwarg = "file_id"
    login_url = reverse_lazy("core:login")

    def get_object(self, queryset=None):
        workspace = get_object_or_404(
            LatexWorkspace,
            slug=self.kwargs["slug"],
            bucket__owner=self.request.user
        )

        return get_object_or_404(
            LatexFile,
            id=self.kwargs["file_id"],
            workspace=workspace
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        latex_file = self.get_object()
        vault_file = latex_file.vault_file

        # Read file content safely
        try:
            content = vault_file.file.read().decode("utf-8")
        except Exception:
            content = "[Unable to read file content]"

        context["workspace"] = latex_file.workspace
        context["content"] = content
        context["can_compile"] = (
            latex_file.file_type == "tex"
            or vault_file.title.lower().endswith(".tex")
        )

        context["compile_runs"] = (
            CompileRun.objects
            .filter(latex_file=latex_file)
            .order_by("-started_at")[:10]
        )
        return PageProcessor().decorate(context, self.request)


@csrf_exempt
def save_file(request, file_id):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    try:
        lf = LatexFile.objects.select_related("vault_file").get(id=file_id)
    except LatexFile.DoesNotExist:
        return JsonResponse({"error": "File not found"}, status=404)

    content = request.POST.get("content", "")

    vault_file = lf.vault_file

    # Write text into the file
    try:
        # Overwrite the file on disk
        with vault_file.file.open("w") as f:
            f.write(content)

        vault_file.save()

        return JsonResponse({"status": "ok"})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def compile_latex(request, file_id):
    try:
        lf = LatexFile.objects.select_related(
            "vault_file", "workspace__bucket"
        ).get(id=file_id)
    except LatexFile.DoesNotExist:
        return JsonResponse({"error": "File not found"}, status=404)

    run = CompileRun.objects.create(
        workspace=lf.workspace,
        latex_file=lf,
        status=CompileRun.PENDING,
    )

    if celery_available():
        compile_latex_task.delay(file_id, run.id)
        return JsonResponse({"status": "queued", "run_id": run.id})

    run.status = CompileRun.RUNNING
    run.save(update_fields=["status"])

    try:
        pdf_vault, log = compile_tex_to_pdf(lf.vault_file, lf.workspace)
    except Exception as exc:
        run.status = CompileRun.FAILED
        run.log = str(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "log", "finished_at"])
        return JsonResponse({
            "status": "failed",
            "run_id": run.id,
            "log": run.log,
            "error": "Compilation failed.",
            "celery_unavailable": True,
            "compiled_inline": True,
        }, status=500)

    run.status = CompileRun.SUCCESS
    run.log = log
    run.pdf_url = pdf_vault.get_public_url()
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "log", "pdf_url", "finished_at"])

    return JsonResponse({
        "status": "ok",
        "run_id": run.id,
        "pdf_url": run.pdf_url,
        "celery_unavailable": True,
        "compiled_inline": True,
    })


def compile_status(request, run_id):
    try:
        run = CompileRun.objects.get(id=run_id)
    except CompileRun.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

    return JsonResponse({
        "status": run.status,
        "log": run.log,
        "pdf_url": run.pdf_url,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    })


@login_required
def compile_history_json(request, file_id):
    lf = get_object_or_404(
        LatexFile,
        id=file_id,
        workspace__bucket__owner=request.user,
    )
    runs = CompileRun.objects.filter(latex_file=lf).order_by("-started_at")[:20]
    return JsonResponse({
        "runs": [
            {
                "id": r.id,
                "status": r.status,
                "started_at": r.started_at.isoformat(),
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "pdf_url": r.pdf_url,
                "log_snippet": r.log[:300] if r.log else "",
            }
            for r in runs
        ]
    })


@csrf_exempt
@login_required
def create_workspace(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    name = request.POST.get("name")
    bucket_id = request.POST.get("bucket_id")

    if not name or not bucket_id:
        return JsonResponse({"error": "Missing fields"}, status=400)

    try:
        bucket = Bucket.objects.get(id=bucket_id, owner=request.user)
    except Bucket.DoesNotExist:
        return JsonResponse({"error": "Invalid bucket"}, status=403)

    workspace = LatexWorkspace.objects.create(
        name=name,
        slug=slugify(name),
        bucket=bucket
    )
    url = reverse("texlab:workspace_detail", kwargs={"slug": workspace.slug})
    return JsonResponse({
        "status": "ok",
        "workspace_id": workspace.id,
        "slug": workspace.slug,
        "url": url
    })


@csrf_exempt
@login_required
def create_file(request, workspace_slug):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    name = request.POST.get("name")
    if not name:
        return JsonResponse({"error": "Missing file name"}, status=400)

    # --- Validate extension ---
    if "." not in name:
        return JsonResponse({"error": "Filename must include an extension"}, status=400)

    base, ext = name.rsplit(".", 1)
    ext = ext.lower()

    VAULT_TYPE_MAP = {
        "tex": "text",
        "sty": "text",
        "bib": "text",
        "txt": "text",
        "pdf": "pdf",
    }
    LATEX_TYPE_MAP = {
        "tex": "tex",
        "sty": "sty",
        "bib": "bib",
        "pdf": "pdf",
    }

    vault_file_type = VAULT_TYPE_MAP.get(ext, "text")
    latex_file_type = LATEX_TYPE_MAP.get(ext, "other")

    # Get workspace
    try:
        workspace = LatexWorkspace.objects.get(
            slug=workspace_slug,
            bucket__owner=request.user
        )
    except LatexWorkspace.DoesNotExist:
        return JsonResponse({"error": "Invalid workspace"}, status=403)

    # --- Option B: try exact name, then auto‑rename ---
    base_slug = slugify(base)
    key = f"{base_slug}.{ext}"
    counter = 1

    while VaultFile.objects.filter(key=key, bucket=workspace.bucket).exists():
        key = f"{base_slug}-{counter}.{ext}"
        counter += 1

    # Create VaultFile
    vault_file = VaultFile.objects.create(
        title=name,
        bucket=workspace.bucket,
        owner=request.user,
        key=key,
        file_type=vault_file_type,
    )

    # Initial content for text-like files
    if ext in ("tex", "sty", "bib", "txt"):
        initial_content = "% Auto‑generated file\n"
        content = ContentFile(initial_content)
    else:
        content = ContentFile(b"")

    vault_file.file.save(key, content, save=True)

    # Create LatexFile entry
    latex_file = LatexFile.objects.create(
        workspace=workspace,
        vault_file=vault_file,
        file_type=latex_file_type
    )

    url = reverse(
        "texlab:file_display",
        kwargs={"slug": workspace.slug, "file_id": latex_file.id}
    )

    return JsonResponse({
        "status": "ok",
        "file_id": latex_file.id,
        "url": url
    })




@csrf_exempt
@login_required
def delete_file(request, file_id):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    # Fetch LatexFile + ensure user owns the workspace
    try:
        lf = LatexFile.objects.select_related(
            "vault_file",
            "workspace__bucket"
        ).get(id=file_id, workspace__bucket__owner=request.user)
    except LatexFile.DoesNotExist:
        return JsonResponse({"error": "File not found or access denied"}, status=404)

    vault_file = lf.vault_file

    try:
        # Delete the actual stored file
        if vault_file.file:
            vault_file.file.delete(save=False)

        # Delete DB entries
        vault_file.delete()
        lf.delete()

        return JsonResponse({"status": "ok"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def bucket_images_json(request, workspace_slug):
    workspace = get_object_or_404(
        LatexWorkspace,
        slug=workspace_slug,
        bucket__owner=request.user,
    )
    images = VaultFile.objects.filter(
        bucket=workspace.bucket,
        file_type="image",
    ).order_by("title")
    return JsonResponse({
        "images": [
            {"title": img.title or img.key}
            for img in images
        ]
    })


@csrf_exempt
@login_required
def delete_workspace(request, slug):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    # Fetch workspace + ensure user owns it
    try:
        workspace = LatexWorkspace.objects.select_related("bucket").get(
            slug=slug,
            bucket__owner=request.user
        )
    except LatexWorkspace.DoesNotExist:
        return JsonResponse({"error": "Workspace not found or access denied"}, status=404)

    try:
        # Delete all files inside the workspace
        for lf in workspace.files.select_related("vault_file"):
            vault_file = lf.vault_file

            # Delete stored file
            if vault_file.file:
                vault_file.file.delete(save=False)

            vault_file.delete()
            lf.delete()

        # Delete workspace itself
        workspace.delete()

        return JsonResponse({"status": "ok"})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)




