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

from .forms import NotebookForm
from .models import Cell, ComputeKernel, KernelDependency, LambdaFunction, Notebook
from .tasks import execute_cell_task
from .kernel import KernelClient


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
    cell = Cell.objects.get(id=cell_id)

    if "content" in request.data:
        cell.content = request.data["content"]
    cell.save()

    execute_cell_task(cell.id)
    cell.refresh_from_db()

    return Response({
        "stdout": cell.stdout,
        "stderr": cell.stderr,
        "execution_count": cell.execution_count,
        "rich_output": cell.rich_output,
    })


# ---------------------------------------------------------
#  Notebook Kernel Management
# ---------------------------------------------------------

client = KernelClient(addr=getattr(settings, "KERNEL_SERVER_ADDR", "tcp://127.0.0.1:5555"))

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

    result = client.start(notebook.id, payload)
    result["installing"] = [
        d["package_name"] + (d["version_spec"] or "") for d in deps
    ]
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
    if result.get("running"):
        return Response({"status": "running"})
    return Response({"status": "stopped"})


# ---------------------------------------------------------
#  Cell CRUD
# ---------------------------------------------------------

@api_view(["POST"])
def create_cell(request, notebook_id):
    notebook = get_object_or_404(Notebook, id=notebook_id)

    last_cell = notebook.cells.order_by("-position").first()
    next_position = (last_cell.position + 1) if last_cell else 1

    cell = Cell.objects.create(
        notebook=notebook,
        cell_type="code",
        content="",
        position=next_position,
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
