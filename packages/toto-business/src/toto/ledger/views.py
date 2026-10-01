"""Reading a chain: the ordered list, one block, the exports, the verdict.

Every page here addresses a chain by its uid and never asks what owns it. That
is what keeps this app reusable — `toto.company` links to these routes, and a
later app can bind its own chains without a line changing here.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from toto.ledger import checkpoint as checkpoint_service
from toto.ledger.models import Ledger, LedgerCheckpoint, LedgerEntry
from toto.ledger.services import chain, export
from toto.ui import PageProcessor


def ledger_render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _ledger(uid):
    return get_object_or_404(Ledger, uid=uid)


@login_required
def index(request):
    ledgers = list(Ledger.objects.order_by("name"))
    for ledger in ledgers:
        ledger.block_count = ledger.entries.count()
    return ledger_render(request, "ledger/index.html", {"ledgers": ledgers})


@login_required
def detail(request, uid):
    """The chain, as a diagram and as a table.

    The table is not a fallback in the apologetic sense — for a chain it is
    often the better read, because "block 41 came after block 40" is a fact
    about a list, and a list says it more plainly than a graph does.
    """
    ledger = _ledger(uid)
    entries = list(
        ledger.entries.select_related("ledger", "actor").order_by("sequence")
    )
    return ledger_render(request, "ledger/detail.html", {
        "ledger": ledger,
        "entries": entries,
        "algorithm": chain.chain_algorithm(ledger),
        "graph_url": reverse("ledger:graph", args=[ledger.uid]),
        "verification": chain.verify(ledger),
    })


@login_required
def block(request, uid, sequence):
    ledger = _ledger(uid)
    entry = get_object_or_404(
        LedgerEntry.objects.select_related("ledger", "actor"),
        ledger=ledger, sequence=sequence,
    )
    return ledger_render(request, "ledger/block.html", {
        "ledger": ledger,
        "entry": entry,
        "block_xml": chain.entry_block_xml(entry),
        "recomputed_hash": chain.entry_hash(entry),
        "previous": ledger.entries.filter(sequence=entry.sequence - 1).first(),
        "next": ledger.entries.filter(sequence=entry.sequence + 1).first(),
    })


@login_required
def verify(request, uid):
    """The verdict, and where it stopped. Reads only — no button changes data."""
    ledger = _ledger(uid)
    result = chain.verify(ledger)
    return ledger_render(request, "ledger/verify.html", {
        "ledger": ledger,
        "result": result,
        "algorithm": chain.chain_algorithm(ledger),
        "failed_block": (
            ledger.entries.filter(sequence=result.first_bad_sequence).first()
            if result.first_bad_sequence else None
        ),
    })


@login_required
def export_xml(request, uid):
    ledger = _ledger(uid)
    response = HttpResponse(
        export.chain_document(ledger), content_type="application/xml; charset=utf-8",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{export.export_filename(ledger, "xml")}"'
    )
    return response


@login_required
def export_zip(request, uid):
    ledger = _ledger(uid)
    response = HttpResponse(export.chain_zip(ledger), content_type="application/zip")
    response["Content-Disposition"] = (
        f'attachment; filename="{export.export_filename(ledger, "zip")}"'
    )
    return response


@login_required
def graph(request, uid):
    """The chain as Cytoscape elements: one node per block, linked in order."""
    ledger = _ledger(uid)
    nodes = []
    edges = []
    previous_id = ""
    for entry in ledger.entries.order_by("sequence"):
        node_id = f"block-{entry.sequence}"
        nodes.append({
            "id": node_id,
            "label": f"#{entry.sequence}\n{entry.entry_hash[:12]}",
            "type": "ledger_entry",
            "url": reverse("ledger:block", args=[ledger.uid, entry.sequence]),
        })
        if previous_id:
            edges.append({"source": previous_id, "target": node_id,
                          "type": "parent_child"})
        previous_id = node_id
    return JsonResponse({"nodes": nodes, "edges": edges})


# ---------------------------------------------------------------------------
# Checkpoints — the head of the chain, on paper
# ---------------------------------------------------------------------------


def _qr(payload: str) -> str:
    from toto.core.qr import QRError, render_data_uri

    try:
        return render_data_uri(payload)
    except QRError:
        # No picture is survivable; the payload text verifies on its own.
        return ""


@login_required
def checkpoints(request, uid):
    ledger = _ledger(uid)
    rows = []
    for stored in LedgerCheckpoint.objects.filter(
        scope_type=checkpoint_service.SCOPE_TYPE, scope_id=str(ledger.uid),
    ):
        rows.append({
            "checkpoint": stored,
            "result": checkpoint_service.verify_stored(stored),
            "qr": _qr(stored.payload),
        })
    matching, failing = checkpoint_service.bracket(
        checkpoint_service.SCOPE_TYPE, str(ledger.uid),
    )
    return ledger_render(request, "ledger/checkpoints.html", {
        "ledger": ledger,
        "algorithm": chain.chain_algorithm(ledger),
        "rows": rows,
        "newest_matching": matching,
        "oldest_failing": failing,
    })


@login_required
@require_POST
def checkpoint_take(request, uid):
    ledger = _ledger(uid)
    try:
        stored = checkpoint_service.take(
            ledger=ledger, by=request.user, note=request.POST.get("note", "")[:200],
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Checkpoint taken at block {stored.entry_count}. Print it, or keep "
            "a photograph somewhere that is not this database — that copy is "
            "what makes it evidence.",
        )
    return redirect("ledger:checkpoints", uid=ledger.uid)


@login_required
def checkpoint_verify(request, uid):
    """Paste a payload, or upload a photograph of one."""
    ledger = _ledger(uid)
    result = None
    payload = ""
    error = ""

    if request.method == "POST":
        payload = (request.POST.get("payload") or "").strip()
        photo = request.FILES.get("photo")
        if photo is not None and not payload:
            from toto.core.qr import QRError, read

            try:
                payload = read(photo.read())
            except QRError as exc:
                error = str(exc)
        if payload and not error:
            try:
                result = checkpoint_service.verify_text(payload)
            except checkpoint_service.PayloadError as exc:
                error = str(exc)
        elif not payload and not error:
            error = "Paste a checkpoint's text, or upload a photograph of its code."

    return ledger_render(request, "ledger/checkpoint_verify.html", {
        "ledger": ledger,
        "result": result,
        "payload": payload,
        "error": error,
    })


# ---------------------------------------------------------------------------
# PDF — built here, rendered by aralia
# ---------------------------------------------------------------------------


@login_required
@require_POST
def export_pdf(request, uid):
    """Queue a PDF of this chain. Takes a fresh checkpoint for the QR.

    The QR is taken at export time rather than reusing an old one on purpose:
    a printed ledger whose code attests to a state from three months ago would
    verify as MATCH_GROWN and tell the reader nothing about the document in
    their hand.
    """
    from toto.documents import builders, services

    ledger = _ledger(uid)
    payload = ""
    try:
        payload = checkpoint_service.take(
            ledger=ledger, by=request.user, note="taken for a PDF export",
        ).payload
    except ValueError:
        pass          # an empty chain has nothing to attest; the PDF still renders

    html = builders.ledger_document(ledger, checkpoint_payload=payload)
    try:
        run = services.export(html, user=request.user, label=f"ledger {ledger.key}")
    except services.ExportRefused as exc:
        messages.error(request, str(exc))
        return redirect("ledger:detail", uid=ledger.uid)

    messages.success(request, services.queued_message())
    return redirect(f"{reverse('ledger:detail', args=[ledger.uid])}?run={run.pk}")
