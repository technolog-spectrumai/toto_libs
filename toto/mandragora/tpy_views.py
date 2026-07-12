"""
File-backed notebook editor for ``.tpy`` vault files.

Unlike the database-backed :mod:`toto.mandragora.views` (Notebook / Cell
models), these views treat a single ``.tpy`` vault file as the whole notebook.
The XML file is parsed into memory when the editor opens and serialized back
when the user presses Save — see :mod:`toto.mandragora.tpy_format`.

Cell execution reuses the mandragora kernel server (ZeroMQ) with a per-file
session id (``tpy:<pk>``), so variables persist across cells just like the
database-backed notebook.  Execution is synchronous: a cell run blocks on the
kernel reply, bounded by the kernel's per-cell timeout.
"""

from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from toto.editor.views import BaseFileDisplayView
from toto.ui import PageProcessor
from toto.vault.models import VaultFile

from . import tpy_format
from .kernel import KernelClient

client = KernelClient(addr=getattr(settings, "KERNEL_SERVER_ADDR", "tcp://127.0.0.1:5555"))


def _session_id(file_pk) -> str:
    return f"tpy:{file_pk}"


def _read_raw(vault_file: VaultFile) -> str:
    """Raw UTF-8 text of the vault file via a fresh storage handle (doesn't touch
    the FieldFile's cached handle, so a later write in the same request is safe)."""
    with vault_file.file.storage.open(vault_file.file.name, "rb") as fh:
        return fh.read().decode("utf-8")


def _get_owned_file(request, file_pk) -> VaultFile:
    """Fetch a notebook vault file owned by the user and validate its content.

    Notebooks are ordinary ``.xml`` files now (``file_type="xml"``); the legacy
    ``notebook`` type is still accepted. Only files whose content is a
    ``<notebook>`` open here, so a contract or other XML can't reach the editor.
    """
    vf = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner"),
        pk=file_pk,
        owner=request.user,
        file_type__in=["xml", "notebook"],
    )
    try:
        if not tpy_format.is_notebook(_read_raw(vf)):
            raise Http404("Not a notebook.")
    except (FileNotFoundError, UnicodeDecodeError, ValueError):
        raise Http404("Not a notebook.")
    return vf


def _read_notebook(vault_file: VaultFile) -> tpy_format.TpyNotebook:
    try:
        raw = vault_file.file.read().decode("utf-8")
    except Exception:
        raw = ""
    try:
        return tpy_format.loads(raw)
    except tpy_format.TpyParseError:
        # Corrupt / non-tpy content — start from a blank notebook rather than
        # blowing up the editor.  Saving will overwrite with valid XML.
        return tpy_format.new_notebook(title=vault_file.title)


def _collect_bucket_files(vault_file: VaultFile) -> dict:
    """Return {title: abs_path} for sibling files in the same bucket (local storage)."""
    if not vault_file.bucket_id:
        return {}
    result: dict[str, str] = {}
    siblings = (
        VaultFile.objects
        .filter(bucket_id=vault_file.bucket_id)
        .exclude(pk=vault_file.pk)
        .order_by("title")
    )
    for vf in siblings:
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


# ---------------------------------------------------------------------------
# Index / create
# ---------------------------------------------------------------------------

NOTEBOOK_LIST_CAP = 300


class TpyIndexView(LoginRequiredMixin, View):
    """List the current user's notebooks. Notebooks are ordinary ``.xml`` vault
    files whose root is ``<notebook>``; the editor is owner-only, so the index
    surfaces the notebooks the user owns and can open."""

    template_name = "mandragora/tpy_index.html"
    login_url = reverse_lazy("core:login")

    def get(self, request):
        from toto.vault.views import new_file_picker_json

        notebooks = []
        files = (
            VaultFile.objects.select_related("bucket", "directory")
            .filter(owner=request.user, file_type__in=["xml", "notebook"], is_encrypted=False)
            .order_by("-uploaded_at")[:NOTEBOOK_LIST_CAP]
        )
        for vf in files:
            try:
                raw = _read_raw(vf)
            except Exception:
                continue
            if not tpy_format.is_notebook(raw):
                continue
            try:
                nb = tpy_format.loads(raw)
            except tpy_format.TpyParseError:
                continue
            bname = vf.bucket.name if vf.bucket else "—"
            location = f"{bname} / {vf.directory.full_path()}" if vf.directory_id else bname
            notebooks.append({
                "title": nb.title or vf.title,
                "cell_count": len(nb.cells),
                "dependency_count": len(nb.dependencies),
                "location": location,
                "open_url": reverse("mandragora:tpy_display", args=[vf.pk]),
            })

        buckets_json, directories_json = new_file_picker_json(request.user)
        context = PageProcessor().decorate(
            {
                "notebooks": notebooks,
                "buckets_json": buckets_json,
                "directories_json": directories_json,
                "create_url": reverse("mandragora:tpy_create"),
            },
            request,
        )
        return render(request, self.template_name, context)


class TpyCreateView(LoginRequiredMixin, View):
    """Create a blank notebook as an ordinary ``.xml`` vault file in a chosen
    bucket/directory, then open the editor. Reuses the shared new-file picker."""

    login_url = reverse_lazy("core:login")

    def post(self, request):
        from django.core.files.base import ContentFile
        from django.utils.text import slugify
        from toto.vault.views import _unique_file_key, resolve_new_file_target

        bucket, directory = resolve_new_file_target(
            request.user, request.POST.get("bucket_id"), request.POST.get("directory_id"),
        )
        raw_name = (request.POST.get("filename") or "").strip()
        base = raw_name[:-4] if raw_name.lower().endswith(".xml") else raw_name
        base = base.strip() or "notebook"
        title = (request.POST.get("title") or "").strip() or base

        xml_bytes = tpy_format.dumps(tpy_format.new_notebook(title=title)).encode("utf-8")
        filename = f"{base}.xml"

        vf = VaultFile(
            owner=request.user, title=filename, key=_unique_file_key(slugify(base), bucket),
            file_type="xml", bucket=bucket, directory=directory, is_public=False,
        )
        vf.file.save(filename, ContentFile(xml_bytes), save=False)
        vf.content_hash = hashlib.sha256(xml_bytes).hexdigest()
        vf.file_size_bytes = len(xml_bytes)
        vf.save()
        return redirect("mandragora:tpy_display", file_pk=vf.pk)


# ---------------------------------------------------------------------------
# Display / save
# ---------------------------------------------------------------------------

class TpyDisplayView(LoginRequiredMixin, View):
    template_name = "mandragora/tpy_display.html"
    login_url = reverse_lazy("core:login")

    def get(self, request, file_pk):
        vault_file = _get_owned_file(request, file_pk)
        if vault_file.is_encrypted:
            from toto.vault.access import encrypted_lock_response
            return encrypted_lock_response(request, vault_file)
        notebook = _read_notebook(vault_file)

        if vault_file.directory:
            directory_name = vault_file.directory.name
        elif vault_file.bucket:
            directory_name = vault_file.bucket.name
        else:
            directory_name = "—"

        context = PageProcessor().decorate(
            {
                "vault_file": vault_file,
                "directory_name": directory_name,
                "has_bucket": vault_file.bucket_id is not None,
                # Hydration payload — the template emits this via {{ ...|json_script }},
                # which JSON-encodes the dict, so pass the dict (not a pre-encoded string).
                "notebook_json": notebook.to_dict(),
                "save_url": reverse("mandragora:tpy_save", args=[file_pk]),
                "run_url": reverse("mandragora:tpy_run_cell", args=[file_pk]),
                "delete_url": reverse("mandragora:tpy_delete", args=[file_pk]),
                "start_url": reverse("mandragora:tpy_start_kernel", args=[file_pk]),
                "stop_url": reverse("mandragora:tpy_stop_kernel", args=[file_pk]),
                "status_url": reverse("mandragora:tpy_kernel_status", args=[file_pk]),
                **BaseFileDisplayView.gitvault_context(vault_file),
            },
            request,
        )
        return render(request, self.template_name, context)


@csrf_exempt
def tpy_save(request, file_pk):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    vault_file = _get_owned_file(request, file_pk)
    if vault_file.is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."}, status=403)

    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError) as exc:
        return JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)

    notebook = tpy_format.TpyNotebook.from_dict(payload)
    xml = tpy_format.dumps(notebook)

    try:
        with vault_file.file.open("w") as f:
            f.write(xml)
        vault_file.save()
        return JsonResponse({"status": "ok"})
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


@csrf_exempt
def tpy_delete(request, file_pk):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)
    vault_file = _get_owned_file(request, file_pk)
    # Best-effort: shut down any running kernel for this notebook first.
    try:
        client.stop(_session_id(file_pk))
    except Exception:
        pass
    vault_file.file.delete(save=False)
    vault_file.delete()
    return JsonResponse({"status": "ok", "redirect": "/vault/"})


# ---------------------------------------------------------------------------
# Kernel management (session keyed by the vault file)
# ---------------------------------------------------------------------------

@csrf_exempt
def tpy_start_kernel(request, file_pk):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    vault_file = _get_owned_file(request, file_pk)
    if vault_file.is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."}, status=403)

    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        payload = {}

    deps = []
    for raw in payload.get("dependencies") or []:
        name = (raw.get("package_name") or "").strip()
        if name:
            deps.append({"package_name": name, "version_spec": (raw.get("version_spec") or "").strip()})

    config = {
        "kernel_name": "python3",
        "timeout_ms": 30_000,
        "env": {},
        "dependencies": deps,
    }
    vault_files = _collect_bucket_files(vault_file)
    if vault_files:
        config["vault_files"] = vault_files

    result = client.start(_session_id(file_pk), config, startup_timeout_ms=120_000)
    result["installing"] = [d["package_name"] + (d["version_spec"] or "") for d in deps]

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
        return JsonResponse(result, status=503)

    return JsonResponse(result)


@csrf_exempt
def tpy_stop_kernel(request, file_pk):
    _get_owned_file(request, file_pk)
    result = client.stop(_session_id(file_pk))
    return JsonResponse(result)


@login_required
def tpy_kernel_status(request, file_pk):
    _get_owned_file(request, file_pk)
    result = client.status(_session_id(file_pk))
    if "error" in result:
        return JsonResponse({
            "status": "unreachable",
            "error": result["error"],
            "error_detail": "Cannot reach the kernel server. Check that the kernel_server container is running.",
        })
    if result.get("running"):
        return JsonResponse({"status": "running"})
    return JsonResponse({"status": "stopped"})


# ---------------------------------------------------------------------------
# Cell execution
# ---------------------------------------------------------------------------

@csrf_exempt
def tpy_run_cell(request, file_pk):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    if _get_owned_file(request, file_pk).is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."}, status=403)

    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError) as exc:
        return JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)

    code = payload.get("code", "")
    response = client.execute(_session_id(file_pk), code)

    if "error" in response:
        error = response["error"]
        if error == "kernel_not_running":
            detail = "The kernel is not running. Press “Start kernel” first."
        elif error == "kernel_server_timeout":
            detail = "The kernel server did not respond in time."
        else:
            detail = error
        return JsonResponse({"status": "error", "error": error, "error_detail": detail}, status=503)

    return JsonResponse({
        "status": "ok",
        "stdout": response.get("stdout", ""),
        "stderr": response.get("stderr", ""),
        "execution_count": response.get("execution_count") or 0,
        "rich_output": response.get("rich_output") or [],
    })
