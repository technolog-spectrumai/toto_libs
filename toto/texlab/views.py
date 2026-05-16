import uuid

from django.core.files.base import ContentFile
from django.views.generic import ListView
from toto.ui import PageProcessor
from django.views.generic import DetailView
from django.shortcuts import get_object_or_404
from toto.ui import PageProcessor
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from toto.texlab.models import LatexFile
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
        lf = LatexFile.objects.select_related("vault_file", "workspace__bucket").get(id=file_id)
    except LatexFile.DoesNotExist:
        return JsonResponse({"error": "File not found"}, status=404)

    try:
        pdf_vault, log = compile_tex_to_pdf(lf.vault_file, lf.workspace)
    except RuntimeError as e:
        return JsonResponse({
            "error": "Compilation failed",
            "log": str(e)
        }, status=400)
    except Exception as e:
        # Unexpected error
        return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({
        "status": "ok",
        "pdf_url": pdf_vault.get_public_url(),
        "log": log
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

    EXT_MAP = {
        "tex": "text",
        "sty": "text",
        "bib": "text",
        "txt": "text",
        "pdf": "pdf",
    }

    file_type = EXT_MAP.get(ext, "text")

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
        file_type=file_type,
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
        file_type=file_type
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






