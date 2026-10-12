"""The timeline's two doors (2026-10-12; ``timeline.py`` has the rules).

* ``file/<pk>/history/``: a file's history, a page of its own (who created,
  edited, opened or downloaded it, and when), twenty rows a page. The file
  list's History action comes here (the owner, 2026-10-12: "do not show long
  history table in modal"; it was a dialog fed JSON for a few hours). For
  whoever may read the file; a file somebody may not read is a 404 here,
  the answer a missing one gets.
* ``metrics/<slug>/history/``: a bucket's page of rows. For its owner and
  superusers, 404 otherwise, as the bucket's own page.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_safe

from toto.ui import PageProcessor

from . import timeline
from .models import Bucket, VaultFile


@login_required
@require_safe
def file_history(request, pk: int):
    # Trashed rows too: a file's history is read from the Trash as well.
    vault_file = VaultFile.all_objects.select_related("bucket", "directory").filter(pk=pk).first()
    if not timeline.may_read_file_timeline(request.user, vault_file):
        raise Http404("No such file.")
    page = Paginator(timeline.of_file(vault_file), timeline.FILE_PAGE).get_page(
        request.GET.get("page"))
    context = {
        "active_tab": "files",
        "file": vault_file,
        "bucket": vault_file.bucket,
        "trashed": vault_file.trashed_at is not None,
        "rows": [timeline.row(event) for event in page.object_list],
        "page_obj": page,
        "is_paginated": page.has_other_pages(),
        "total": page.paginator.count,
    }
    response = render(request, "vault/file_history.html",
                      PageProcessor().decorate(context, request))
    response["Cache-Control"] = "no-store"
    return response


#: The largest note the viewer takes: the renderer's own limit (render.ts).
MARKDOWN_MAX_BYTES = 512 * 1024


@login_required
@require_safe
def markdown_view(request, pk: int):
    """A Markdown file of the vault, rendered (2026-10-12): the page behind
    the Play button of a ``.md`` file. For whoever may read the file (404
    otherwise). The text reaches the page as data and is rendered in the
    browser by Morion's renderer (``mdview/``), whose allow-list decides what
    of it becomes markup. An encrypted file and one over the limit are not
    read at all."""
    from . import access

    vault_file = VaultFile.objects.select_related("bucket", "directory").filter(pk=pk).first()
    if vault_file is None or not access.may_read(request.user, vault_file):
        raise Http404("No such file.")
    source, refusal = "", ""
    if vault_file.is_encrypted:
        refusal = "encrypted"
    elif (vault_file.file.size or 0) > MARKDOWN_MAX_BYTES:
        refusal = "too_large"
    else:
        vault_file.file.open("rb")
        try:
            source = vault_file.file.read().decode("utf-8", errors="replace")
        finally:
            vault_file.file.close()
        timeline.read(vault_file, inline=True)          # the file's history: opened
    context = {
        "active_tab": "files",
        "file": vault_file,
        "bucket": vault_file.bucket,
        "markdown_source": source,
        "markdown_refusal": refusal,
        "markdown_max_kb": MARKDOWN_MAX_BYTES // 1024,
    }
    response = render(request, "vault/markdown_view.html", PageProcessor().decorate(context, request))
    response["Cache-Control"] = "no-store"
    return response


#: The largest table the sheet viewer takes.
SHEET_MAX_BYTES = 5 * 1024 * 1024
#: The file types it opens, and what the page calls each (main.ts ``open``).
SHEET_KINDS = {"csv": "csv", "uson": "uson", "sheet": "sheet"}


@login_required
@require_safe
def sheet_view(request, pk: int):
    """A CSV file or a ``.uson`` sheet of the vault, drawn as a grid and READ
    ONLY (2026-10-12): the page behind their Play button. For whoever may
    read the file (404 otherwise, and for a file that is no table). The text
    reaches the page as data; Morion's sheet format and Univer (``sheetview/``)
    draw it in the browser, and nothing is ever written back."""
    from . import access

    vault_file = VaultFile.objects.select_related("bucket", "directory").filter(pk=pk).first()
    if vault_file is None or not access.may_read(request.user, vault_file):
        raise Http404("No such file.")
    kind = SHEET_KINDS.get(vault_file.file_type)
    if kind is None:
        raise Http404("Not a table.")
    source, refusal = "", ""
    if vault_file.is_encrypted:
        refusal = "encrypted"
    elif (vault_file.file.size or 0) > SHEET_MAX_BYTES:
        refusal = "too_large"
    else:
        vault_file.file.open("rb")
        try:
            source = vault_file.file.read().decode("utf-8-sig", errors="replace")
        finally:
            vault_file.file.close()
        timeline.read(vault_file, inline=True)          # the file's history: opened
    context = {
        "active_tab": "files",
        "file": vault_file,
        "bucket": vault_file.bucket,
        "sheet_kind": kind,
        "sheet_source": source,
        "sheet_refusal": refusal,
        "sheet_max_mb": SHEET_MAX_BYTES // (1024 * 1024),
    }
    response = render(request, "vault/sheet_view.html", PageProcessor().decorate(context, request))
    response["Cache-Control"] = "no-store"
    return response


#: The largest deck the player takes: the format's own limit (presentation.ts).
DECK_MAX_BYTES = 8 * 1024 * 1024
DECK_KINDS = ("pxml", "presentation")


@login_required
@require_safe
def deck_view(request, pk: int):
    """A ``.pxml`` presentation of the vault, played (2026-10-12): the page
    behind its Play button. For whoever may read the file (404 otherwise, and
    for a file that is no deck). The text reaches the page as data; Morion's
    deck format and reveal.js (``deckview/``) present it in the browser, every
    block through the allow-list first. Nothing is written back."""
    from . import access

    vault_file = VaultFile.objects.select_related("bucket", "directory").filter(pk=pk).first()
    if vault_file is None or not access.may_read(request.user, vault_file):
        raise Http404("No such file.")
    if vault_file.file_type not in DECK_KINDS:
        raise Http404("Not a presentation.")
    source, refusal = "", ""
    if vault_file.is_encrypted:
        refusal = "encrypted"
    elif (vault_file.file.size or 0) > DECK_MAX_BYTES:
        refusal = "too_large"
    else:
        vault_file.file.open("rb")
        try:
            source = vault_file.file.read().decode("utf-8-sig", errors="replace")
        finally:
            vault_file.file.close()
        timeline.read(vault_file, inline=True)          # the file's history: opened
    context = {
        "active_tab": "files",
        "file": vault_file,
        "bucket": vault_file.bucket,
        "deck_source": source,
        "deck_refusal": refusal,
        "deck_max_mb": DECK_MAX_BYTES // (1024 * 1024),
    }
    response = render(request, "vault/deck_view.html", PageProcessor().decorate(context, request))
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_safe
def bucket_history(request, bucket_slug: str):
    bucket = get_object_or_404(Bucket, slug=bucket_slug)
    if not timeline.may_read_bucket_timeline(request.user, bucket):
        raise Http404("No such bucket.")
    events = timeline.of_bucket(request.user, bucket)
    known = dict(timeline.kinds_named())
    kind = request.GET.get("kind") or ""
    if kind in known:
        events = events.filter(kind=kind)
    else:
        kind = ""
    page = Paginator(events, timeline.PAGE).get_page(request.GET.get("page"))
    context = {
        "active_tab": "metrics",
        "bucket": bucket,
        "rows": [timeline.row(event) for event in page.object_list],
        "page_obj": page,
        "is_paginated": page.has_other_pages(),
        "extra_query": f"&kind={kind}" if kind else "",
        "kind": kind,
        "kinds": timeline.kinds_named(),
        "total": page.paginator.count,
    }
    return render(request, "vault/bucket_history.html", PageProcessor().decorate(context, request))
