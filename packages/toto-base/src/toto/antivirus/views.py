"""Two screens: what has been found, and what you can scan.

Deliberately small. The app's job for now is to own *where* scanning lives; the
reporting on top of it grows later.
"""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor
from toto.vault.filetree import accessible_files, build_file_tree
from toto.vault.models import VaultFile
from toto.vault.scanning import SCANNABLE_TYPES, scan

from . import engine
from .models import ScanPreference, ScanResult, ScanVerdict


def _render(request, template_name, context):
    return render(request, template_name,
                  PageProcessor().decorate(context, request))


def _my_files(user):
    """Files this person may scan — and the ONE definition of that.

    `accessible_files` minus its public arm: a stranger's public file is
    readable, but it is not this person's to screen, and letting anyone queue
    work against anyone's files is how a scan button becomes an amplifier.

    That was the intent before and the code did not carry it:
    ``.exclude(owner__isnull=True)`` drops files with NO owner, not files owned
    by somebody else, so every public file in the vault was scannable by anyone.
    The exclusion now happens where the access rule lives.

    Encrypted files are dropped here too, because ``scan_file`` refuses them —
    and the index builds its tree FROM THIS QUERYSET, so a row can no longer
    appear with a button the endpoint will 404.
    """
    return (accessible_files(user, file_types=list(SCANNABLE_TYPES),
                             include_public=False)
            .filter(is_encrypted=False)
            .exclude(owner__isnull=True))


@login_required
def index(request):
    """Threats found, files scanned, and the files you can scan."""
    mine = _my_files(request.user)
    my_ids = list(mine.values_list("pk", flat=True))

    results = ScanResult.objects.filter(file_id__in=my_ids)
    threats = results.filter(verdict=ScanVerdict.REFUSED)

    # The same queryset the scan endpoint accepts. Anything else lists rows
    # whose button cannot work.
    tree = build_file_tree(request.user, queryset=mine)
    clean_ids = engine.clean_file_ids(mine)

    return _render(request, "antivirus/index.html", {
        "threats_found": threats.count(),
        "files_scanned": results.values("file_id").distinct().count(),
        "threats": threats.select_related("file")[:50],
        "tree": tree,
        "clean_ids": clean_ids,
        "scannable_types": SCANNABLE_TYPES,
        # JSON, not the tuple: Alpine has to parse it, and a Python
        # repr with single quotes is not JavaScript.
        "auto_types_json": json.dumps(list(ScanPreference.types_for(request.user))),
    })


@login_required
@require_POST
def set_preference(request):
    """Which types get screened automatically for this person's own files."""
    chosen = [t for t in request.POST.getlist("types") if t in SCANNABLE_TYPES]
    ScanPreference.objects.update_or_create(
        user=request.user, defaults={"types": chosen})
    return JsonResponse({"ok": True, "types": chosen})


@login_required
@require_POST
def scan_file(request, pk):
    """Scan one file now, on purpose.

    Always runs, whatever the automatic preferences say — pressing the button is
    the deliberate act the preferences exist to be an alternative to.
    """
    vault_file = get_object_or_404(_my_files(request.user), pk=pk)
    if vault_file.is_encrypted:
        raise Http404("Encrypted files cannot be read.")

    try:
        with vault_file.file.open("rb") as handle:
            content = handle.read()
    except (OSError, ValueError):
        return JsonResponse(
            {"ok": False, "error": "The file could not be read."}, status=400)

    verdict = scan(content, file_type=vault_file.file_type,
                   filename=vault_file.title)
    engine.record(vault_file, verdict, user=request.user, door="manual",
                  content=content)

    return JsonResponse({
        "ok": True,
        "clean": verdict.ok,
        "scanned": verdict.scanned,
        "reason": verdict.reason,
        "detail": verdict.detail,
        "line": verdict.line,
    })
