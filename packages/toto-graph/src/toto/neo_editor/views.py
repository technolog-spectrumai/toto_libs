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
from toto.vault import access
from toto.vault.models import VaultFile


def _own_file(user, file_pk) -> VaultFile:
    """A file of this user's, or 404 — its bucket's clearances first.

    The owner filter alone let an owner who lacks their bucket's clearance
    open, save and load here, against "no owner bypass" (2026-10-01; the ACE
    editor, cyprian and sketch closed the same hole on 2026-09-30).
    `access.gate_by_bucket` is the queryset the vault's own doors and those
    editors ask, so a file hidden by its bucket is missing here too.
    """
    files = VaultFile.objects.select_related("bucket", "directory", "owner")
    return get_object_or_404(access.gate_by_bucket(user, files), pk=file_pk, owner=user)


@login_required
def neojson_editor_view(request, file_pk):
    vault_file = _own_file(request.user, file_pk)
    if vault_file.is_encrypted:
        return access.encrypted_lock_response(request, vault_file)

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
            "load_url": reverse("neo_editor:neojson_load", args=[file_pk]),
        },
        request,
    )
    return render(request, "neo_editor/neojson_editor.html", context)


@csrf_exempt
@login_required
def neojson_save_view(request, file_pk):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    vault_file = _own_file(request.user, file_pk)
    if vault_file.is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."}, status=403)
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


@csrf_exempt
@login_required
def neojson_load_view(request, file_pk):
    """Load the editor's NeoJSON into Neo4j (merge | replace) via ravioli's sync engine."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    # The file_pk scopes the request to the user's own file, and a file its
    # bucket hides from them scopes it to nothing.
    _own_file(request.user, file_pk)

    # The SQL→Neo4j sync engine is an optional, retirable app. Degrade gracefully
    # (the editor still saves/edits) if it isn't installed.
    try:
        from toto.sql_neo4j_sync import graphsync
    except ImportError:
        return JsonResponse(
            {"error": "Graph sync engine (toto.sql_neo4j_sync) is not installed."},
            status=400,
        )
    from toto.ravioli.connection import Neo4jClient, is_enabled

    content = request.POST.get("content", "")
    mode = (request.POST.get("mode") or graphsync.MODE_MERGE).strip().lower()
    if mode not in graphsync.MODES:
        return JsonResponse({"error": f"Invalid mode {mode!r}; use 'merge' or 'replace'."}, status=400)

    try:
        graph = neojson.loads(content)
    except neojson.NeoJsonParseError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    errors = neojson.validate(graph)
    if errors:
        return JsonResponse({"error": "Invalid NeoJSON: " + "; ".join(errors)}, status=400)

    if not is_enabled():
        return JsonResponse({"error": "Graph database is disabled (RAVIOLI_ENABLED is False)."}, status=400)

    client = Neo4jClient()
    try:
        summary = graphsync.load_neojson(client, graph, mode=mode)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)
    finally:
        client.close()

    return JsonResponse({"status": "ok", "summary": summary})
