"""Dual-mode .neojson vault editor: Ace JSON (editable) + cytoscape graph (read-only preview).

Hosted in its own app so both the vault (via the VaultEditorPlugin "Edit" button) and
ravioli (the query → NeoJSON export "Open in editor" link) can reuse it. The NeoJSON
format module itself lives in :mod:`toto.ravioli.neojson`.
"""

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from toto.ravioli import neojson
from toto.ui import PageProcessor
from toto.vault.models import VaultFile


@login_required
def neojson_editor_view(request, file_pk):
    vault_file = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner"),
        pk=file_pk,
        owner=request.user,
    )

    try:
        content = vault_file.file.read().decode("utf-8")
    except Exception:
        content = ""
    if not content.strip():
        content = neojson.dumps(neojson.new_graph())

    directory_name = (
        vault_file.directory.name if vault_file.directory
        else (vault_file.bucket.name if vault_file.bucket else "—")
    )

    context = PageProcessor().decorate(
        {
            "vault_file": vault_file,
            "content": content,
            "directory_name": directory_name,
            "save_url": reverse("neo_editor:neojson_save", args=[file_pk]),
        },
        request,
    )
    return render(request, "neo_editor/neojson_editor.html", context)


@csrf_exempt
@login_required
def neojson_save_view(request, file_pk):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    vault_file = get_object_or_404(VaultFile, pk=file_pk, owner=request.user)
    content = request.POST.get("content", "")

    # Validate it is well-formed NeoJSON before overwriting the file.
    try:
        graph = neojson.loads(content)
    except neojson.NeoJsonParseError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    errors = neojson.validate(graph)
    if errors:
        return JsonResponse({"error": "Invalid NeoJSON: " + "; ".join(errors)}, status=400)

    try:
        with vault_file.file.open("w") as f:
            f.write(content)
        vault_file.save()
        return JsonResponse({"status": "ok"})
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)
