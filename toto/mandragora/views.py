from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import ListView, DetailView
from toto.ui import PageProcessor

from rest_framework.decorators import api_view
from rest_framework.response import Response

from toto.celery_utils import celery_available
from toto.core.connectors import ConnectorExecutionError
from .forms import NotebookForm
from .models import Cell, ComputeKernel, KernelDependency, Notebook
from .tasks import execute_cell_task
from .kernel import KernelClient
from toto.workflows.models import LambdaFunction
from .connectors import execute_connector, list_available_connectors, validate_connector


def mandragora_render(request, template_name, context):
    return render(request, template_name, PageProcessor().decorate(context, request))


# ---------------------------------------------------------
#  Notebook List + Detail
# ---------------------------------------------------------

class NotebookListView(LoginRequiredMixin, ListView):
    model = Notebook
    template_name = "mandragora/notebook_list.html"
    context_object_name = "notebooks"
    login_url = reverse_lazy("core:login")

    def get_queryset(self):
        return Notebook.objects.all().order_by("title")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


class NotebookDetailView(LoginRequiredMixin, DetailView):
    model = Notebook
    template_name = "mandragora/notebook_detail.html"
    context_object_name = "notebook"
    slug_field = "slug"
    slug_url_kwarg = "slug"
    login_url = reverse_lazy("core:login")

    def get_object(self, queryset=None):
        return get_object_or_404(
            Notebook,
            slug=self.kwargs["slug"]
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        notebook = self.get_object()
        context["cells"] = notebook.cells.order_by("position")
        context["has_bucket"] = notebook.bucket_id is not None
        return PageProcessor().decorate(context, self.request)


# ---------------------------------------------------------
#  Notebook CRUD
# ---------------------------------------------------------

@login_required
def notebook_create(request):
    if request.method == "POST":
        form = NotebookForm(request.POST)
        if form.is_valid():
            notebook = form.save()
            messages.success(request, "Notebook created.")
            return redirect("mandragora:notebook_detail", slug=notebook.slug)
    else:
        form = NotebookForm()

    return mandragora_render(request, "mandragora/notebook_form.html", {
        "form": form,
        "title": "New notebook",
        "submit_label": "Create notebook",
        "icon": "fa-solid fa-plus",
    })


@login_required
def notebook_update(request, slug):
    notebook = get_object_or_404(Notebook, slug=slug)

    if request.method == "POST":
        form = NotebookForm(request.POST, instance=notebook)
        if form.is_valid():
            notebook = form.save()
            messages.success(request, "Notebook saved.")
            return redirect("mandragora:notebook_detail", slug=notebook.slug)
    else:
        form = NotebookForm(instance=notebook)

    return mandragora_render(request, "mandragora/notebook_form.html", {
        "form": form,
        "notebook": notebook,
        "title": "Edit notebook",
        "submit_label": "Save notebook",
        "icon": "fa-solid fa-pen-to-square",
    })


@login_required
def notebook_delete(request, slug):
    notebook = get_object_or_404(Notebook, slug=slug)

    if request.method == "POST":
        notebook.delete()
        messages.success(request, "Notebook deleted.")
        return redirect("mandragora:notebook_list")

    return mandragora_render(request, "mandragora/notebook_confirm_delete.html", {
        "notebook": notebook,
    })


# ---------------------------------------------------------
#  Helpers
# ---------------------------------------------------------

def ensure_notebook_kernel(notebook: Notebook):
    if notebook.kernel is None:
        kernel = ComputeKernel.objects.create(
            name=f"notebook-kernel-{notebook.id}",
            timeout_ms=5000,
            env={},
        )
        notebook.kernel = kernel
        notebook.save(update_fields=["kernel"])
    return notebook.kernel


def ensure_lambda_kernel(lambda_fn: LambdaFunction):
    if lambda_fn.kernel is None:
        kernel = ComputeKernel.objects.create(
            name=f"lambda-kernel-{lambda_fn.id}",
            timeout_ms=3000,
            env={},
        )
        lambda_fn.kernel = kernel
        lambda_fn.save(update_fields=["kernel"])
    return lambda_fn.kernel


# ---------------------------------------------------------
#  Cell Execution
# ---------------------------------------------------------

@api_view(["POST"])
def run_cell(request, cell_id):
    if not celery_available():
        return Response(
            {
                "error": (
                    "No Celery workers are running. "
                    "Start the kernel server AND a Celery worker before executing cells."
                ),
                "celery_unavailable": True,
            },
            status=503,
        )

    cell = Cell.objects.get(id=cell_id)
    if "content" in request.data:
        cell.content = request.data["content"]
    cell.save()

    result = execute_cell_task.delay(cell.id)
    return Response({"status": "queued", "task_id": result.id})


@api_view(["GET"])
def cell_result(request, cell_id):
    """Poll this endpoint after run_cell returns task_id."""
    from celery.result import AsyncResult

    task_id = request.GET.get("task_id", "")
    if not task_id:
        return Response({"error": "task_id required"}, status=400)

    ar = AsyncResult(task_id)
    if ar.state in ("PENDING", "STARTED", "RECEIVED", "RETRY"):
        return Response({"status": "running"})

    cell = get_object_or_404(Cell, id=cell_id)
    return Response({
        "status": "done" if ar.state == "SUCCESS" else "failed",
        "stdout": cell.stdout,
        "stderr": cell.stderr,
        "execution_count": cell.execution_count,
        "rich_output": cell.rich_output,
    })


# ---------------------------------------------------------
#  Notebook Kernel Management
# ---------------------------------------------------------

client = KernelClient(addr=getattr(settings, "KERNEL_SERVER_ADDR", "tcp://127.0.0.1:5555"))


def _collect_vault_files(notebook) -> dict:
    """Return {title: abs_path} for every file in notebook.bucket (local storage only)."""
    if not notebook.bucket_id:
        return {}
    from toto.vault.models import VaultFile
    result = {}
    for vf in VaultFile.objects.filter(bucket_id=notebook.bucket_id).order_by("title"):
        try:
            path = vf.file.path
        except (ValueError, NotImplementedError):
            continue
        key = vf.title
        if key in result:
            base, dot, ext = key.rpartition(".")
            n = 2
            candidate = f"{base}_{n}.{ext}" if dot else f"{key}_{n}"
            while candidate in result:
                n += 1
                candidate = f"{base}_{n}.{ext}" if dot else f"{key}_{n}"
            key = candidate
        result[key] = path
    return result


@api_view(["GET"])
def notebook_vault_files(request, notebook_id):
    """Return the list of vault files attached to a notebook, for the Files modal."""
    notebook = get_object_or_404(Notebook, id=notebook_id)
    if not notebook.bucket_id:
        return Response({"bucket": None, "files": []})

    from django.urls import reverse
    from toto.vault.models import VaultFile
    bucket = notebook.bucket
    files_data = []
    for vf in VaultFile.objects.filter(bucket=bucket).select_related("directory").order_by("title"):
        try:
            abs_path = vf.file.path
        except (ValueError, NotImplementedError):
            abs_path = None
        try:
            download_url = reverse("vault:public_file", args=[bucket.slug, vf.key])
        except Exception:
            download_url = None
        files_data.append({
            "id": vf.pk,
            "title": vf.title,
            "key": vf.key,
            "file_type": vf.file_type or "",
            "size_bytes": vf.file_size_bytes,
            "is_public": vf.is_public,
            "download_url": download_url,
            "has_local_path": abs_path is not None,
            "directory": vf.directory.name if vf.directory else None,
        })

    return Response({
        "bucket": {"name": bucket.name, "slug": bucket.slug},
        "files": files_data,
    })

@api_view(["POST"])
def start_kernel(request, notebook_id):
    notebook = get_object_or_404(Notebook, id=notebook_id)
    kernel = ensure_notebook_kernel(notebook)

    deps = list(
        kernel.kernel_dependencies.values("package_name", "version_spec")
    )

    payload = {
        "kernel_name": "python3",
        "timeout_ms": kernel.timeout_ms,
        "env": kernel.env or {},
        "dependencies": deps,
    }

    # Inject vault file paths if a bucket is attached
    vault_files = _collect_vault_files(notebook)
    if vault_files:
        payload["vault_files"] = vault_files

    result = client.start(notebook.id, payload, startup_timeout_ms=kernel.startup_timeout_ms)
    result["installing"] = [
        d["package_name"] + (d["version_spec"] or "") for d in deps
    ]

    if "error" in result:
        error = result["error"]
        if error == "kernel_server_timeout":
            result["error_detail"] = (
                "The kernel server did not respond in time. "
                "Kernel startup in Docker can take up to 2 minutes on first use. "
                "Wait a moment and try again."
            )
        else:
            result["error_detail"] = f"Kernel error: {error}"
        return Response(result, status=503)

    return Response(result)


@api_view(["GET"])
def kernel_dependencies(request, notebook_id):
    notebook = get_object_or_404(Notebook, id=notebook_id)
    kernel = ensure_notebook_kernel(notebook)
    deps = list(
        kernel.kernel_dependencies.values(
            "package_name", "version_spec", "install_status"
        )
    )
    auto_close = kernel.auto_close
    return Response({"dependencies": deps, "auto_close": auto_close})


@api_view(["POST"])
def stop_kernel(request, notebook_id):
    notebook = get_object_or_404(Notebook, id=notebook_id)
    ensure_notebook_kernel(notebook)
    result = client.stop(notebook.id)
    return Response(result)


@api_view(["GET"])
def check_kernel(request, notebook_id):
    notebook = get_object_or_404(Notebook, id=notebook_id)
    result = client.status(notebook.id)
    if "error" in result:
        return Response({
            "status": "unreachable",
            "error": result["error"],
            "error_detail": "Cannot reach the kernel server. Check that the kernel_server container is running.",
        })
    if result.get("running"):
        return Response({"status": "running"})
    return Response({"status": "stopped"})


# ---------------------------------------------------------
#  Connector Access
# ---------------------------------------------------------

@api_view(["GET"])
def connector_list(request):
    return Response({"connectors": list_available_connectors()})


@api_view(["POST"])
def connector_execute(request):
    connector_type = request.data.get("connector_type")
    if not connector_type:
        return Response({"error": "connector_type required"}, status=400)

    config = request.data.get("config") or {}
    input_data = request.data.get("input_data") or {}
    errors = validate_connector(connector_type, config)
    if errors:
        return Response({"errors": errors}, status=422)

    try:
        return Response(execute_connector(connector_type, config, input_data))
    except ConnectorExecutionError as exc:
        return Response({"error": str(exc)}, status=400)


# ---------------------------------------------------------
#  Cell CRUD
# ---------------------------------------------------------

@api_view(["POST"])
def create_cell(request, notebook_id):
    notebook = get_object_or_404(Notebook, id=notebook_id)

    cell_type = request.data.get("cell_type", "code")
    if cell_type not in ("code", "markdown"):
        cell_type = "code"
    content  = request.data.get("content", "")
    position = request.data.get("position")

    if position is not None:
        try:
            position = int(position)
        except (TypeError, ValueError):
            position = None

    if position is None:
        last_cell = notebook.cells.order_by("-position").first()
        position = (last_cell.position + 1) if last_cell else 1

    cell = Cell.objects.create(
        notebook=notebook,
        cell_type=cell_type,
        content=content,
        position=position,
    )

    return Response({
        "id": cell.id,
        "position": cell.position,
        "content": cell.content,
        "cell_type": cell.cell_type,
    })


@api_view(["POST"])
def delete_cell(request, cell_id):
    cell = get_object_or_404(Cell, id=cell_id)
    notebook = cell.notebook

    cell.delete()

    for index, c in enumerate(notebook.cells.order_by("position"), start=1):
        if c.position != index:
            c.position = index
            c.save(update_fields=["position"])

    return Response({"status": "deleted"})


# ---------------------------------------------------------
#  Promote Cell → LambdaFunction
# ---------------------------------------------------------

@api_view(["POST"])
def promote_cell_to_lambda(request, cell_id):
    cell = get_object_or_404(Cell, id=cell_id)

    content = request.data.get("content", cell.content)

    function_name = request.data.get(
        "function_name",
        f"notebook_cell_{cell.id}"
    )

    lambda_fn, created = LambdaFunction.objects.update_or_create(
        function_name=function_name,
        defaults={
            "content": content,
            "stdout": "",
            "stderr": "",
        }
    )

    kernel = ensure_lambda_kernel(lambda_fn)

    return Response({
        "status": "created" if created else "updated",
        "lambda_function_id": lambda_fn.id,
        "function_name": lambda_fn.function_name,
        "kernel": kernel.name,
        "content": lambda_fn.content,
    })
