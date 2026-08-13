"""Three endpoints: ask, poll, and the operator's page.

Nothing here decides anything — ``services`` holds every judgement. These
translate HTTP into a run and back.

**Ask is a POST that creates a row and returns immediately.** The answer arrives
by polling ``run_status``, which returns the same payload shape fileservices and
texlab return, so the platform has one polling idiom rather than four.
"""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from toto.ui import PageProcessor

from . import dispatch, services
from .models import AiProvider, AiRun
from .surfaces import registry

#: The largest selection that may be sent. A selection is not a document — the
#: whole billing model rests on that — and 20k characters is already several
#: pages. Bigger than this is a file-level job, which is a different feature.
MAX_SELECTION = 20_000


def _render(request, template_name, context):
    return render(request, template_name,
                  PageProcessor().decorate(context, request))


@login_required
@require_POST
def ask(request):
    """Start one request. Returns ``{run_id}`` or a reason it will not."""
    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    surface_key = (payload.get("surface") or "").strip()
    action_key = (payload.get("action") or "").strip()
    selection = payload.get("selection") or ""
    instruction = (payload.get("instruction") or "").strip()

    surface = registry.get(surface_key)
    if surface is None:
        return JsonResponse({"error": "Unknown surface."}, status=400)
    action = surface.action(action_key)
    if action is None:
        return JsonResponse({"error": "Unknown action."}, status=400)

    if not selection.strip():
        return JsonResponse({"error": "Select something first."}, status=400)
    if len(selection) > MAX_SELECTION:
        return JsonResponse(
            {"error": f"That selection is too long ({len(selection)} characters; "
                      f"the limit is {MAX_SELECTION}). Select less, or use a "
                      f"whole-file action."}, status=400)
    if action.needs_instruction and not instruction:
        return JsonResponse({"error": "This action needs an instruction."},
                            status=400)

    try:
        provider = services.active_provider()
    except services.NotConfigured as exc:
        # 503, not 400: the user did nothing wrong and retrying identically will
        # work the moment an operator switches a provider on.
        return JsonResponse({"error": str(exc)}, status=503)

    # Affordability BEFORE anything is queued — the worst case, because the real
    # cost is not knowable until the answer arrives. See services.check_affordable.
    try:
        services.check_affordable(request.user, provider, selection)
    except Exception as exc:  # noqa: BLE001 - QuotaExceeded / InsufficientFunds
        status = getattr(exc, "status_code", None)
        if status is None:
            raise
        return JsonResponse({"error": str(exc)}, status=status)

    run = dispatch.create_run(user=request.user, surface=surface_key,
                              action=action_key, source_text=selection,
                              instruction=instruction)
    try:
        dispatch.dispatch_run(run)
    except dispatch.CannotQueue as exc:
        dispatch.fail_run(run, str(exc))
        return JsonResponse({"error": str(exc), "run_id": run.pk}, status=503)

    return JsonResponse({"run_id": run.pk, "status": run.status,
                         "status_url": f"/steven/runs/{run.pk}/"})


@login_required
@require_GET
def run_status(request, pk: int):
    """Poll one run. Owner only — a run carries what somebody selected."""
    run = get_object_or_404(AiRun, pk=pk, owner=request.user)
    return JsonResponse(services.run_payload(run))


@login_required
def console(request):
    """What the assistant is, what it costs you, and what you have asked it.

    The one page steven owns. Deliberately small: the feature lives inside the
    editors, and this exists so somebody can answer "is it on, what does it cost
    me, and what did I ask it yesterday" without opening a document.
    """
    from toto.quota import rates

    provider = AiProvider.current()
    runs = AiRun.objects.filter(owner=request.user)[:25]

    card = rates.rate_card()
    return _render(request, "steven/console.html", {
        "provider": provider,
        "configured": bool(provider and provider.secret_id),
        "surfaces": list(registry.all()),
        "runs": runs,
        "price_request": card.get("ai.request"),
        "price_tokens": card.get("ai.tokens_1k"),
        "spend": (rates.spend_by_metric(request.user) or {}),
        "balance": rates.balance_of(request.user),
    })


#: How much of a file may be read into a prompt. Bigger than a selection because
#: the whole point is the whole file, and small enough that one press cannot
#: cost a fortune — at roughly four characters per token this is about 15k
#: tokens, which the wallet check below reserves before anything runs.
MAX_FILE_CHARS = 60_000


@login_required
def file_ask(request, file_pk: int):
    """Ask about a whole file. The wand's page.

    Reached from the vault listing through the ``steven`` file-service plugin,
    which is builder-backed precisely so this works on a host with no
    toto-media-ops: the assistant runs on its own ``AiRun``, not on a
    ``FileServiceRun``.

    **Read access is the vault's rule, not a second one** — the same
    ``may_read`` the download view uses. GET renders the page; POST starts a run
    and hands back an id to poll, exactly like the editor path.
    """
    from toto.vault.access import may_read
    from toto.vault.models import VaultFile

    vault_file = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory"), pk=file_pk)
    if not may_read(request.user, vault_file):
        raise Http404("No such file.")
    if vault_file.is_encrypted:
        raise Http404("Encrypted files cannot be read.")

    surface = registry.get("file")
    if surface is None:
        raise Http404("The assistant has no file surface on this host.")

    if request.method != "POST":
        return _render(request, "steven/file_ask.html", {
            "vault_file": vault_file,
            "surface": surface,
            "actions": surface.actions,
        })

    action = surface.action((request.POST.get("action") or "").strip())
    if action is None:
        return JsonResponse({"error": "Unknown action."}, status=400)
    instruction = (request.POST.get("instruction") or "").strip()
    if action.needs_instruction and not instruction:
        return JsonResponse({"error": "This action needs a question."}, status=400)

    try:
        with vault_file.file.open("rb") as handle:
            text = handle.read().decode("utf-8")
    except (OSError, ValueError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "That file could not be read as text."}, status=400)

    if len(text) > MAX_FILE_CHARS:
        # Truncated rather than refused, and SAID so: refusing a long file makes
        # the feature useless on exactly the documents somebody most wants
        # summarised, and silently truncating would let the answer describe a
        # document nobody sent.
        text = text[:MAX_FILE_CHARS]
        truncated = True
    else:
        truncated = False

    try:
        provider = services.active_provider()
    except services.NotConfigured as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    try:
        services.check_affordable(request.user, provider, text)
    except Exception as exc:  # noqa: BLE001 - QuotaExceeded / InsufficientFunds
        status = getattr(exc, "status_code", None)
        if status is None:
            raise
        return JsonResponse({"error": str(exc)}, status=status)

    run = dispatch.create_run(user=request.user, surface="file",
                              action=action.key, source_text=text,
                              instruction=instruction)
    try:
        dispatch.dispatch_run(run)
    except dispatch.CannotQueue as exc:
        dispatch.fail_run(run, str(exc))
        return JsonResponse({"error": str(exc), "run_id": run.pk}, status=503)

    return JsonResponse({"run_id": run.pk, "status": run.status,
                         "truncated": truncated})


@login_required
@require_GET
def surface_actions(request, key: str):
    """The actions one surface offers, as JSON — what a toolbar renders from.

    Served rather than templated so a toolbar in any of six editors, in three
    packages, does not each grow its own copy of the action list.
    """
    surface = registry.get(key)
    if surface is None:
        raise Http404("No such surface.")
    return JsonResponse({
        "surface": surface.key,
        "label": surface.label,
        "actions": [
            {"key": a.key, "label": str(a.label), "icon": a.icon,
             "needs_instruction": a.needs_instruction,
             "placeholder": str(a.instruction_placeholder)}
            for a in surface.actions
        ],
    })
