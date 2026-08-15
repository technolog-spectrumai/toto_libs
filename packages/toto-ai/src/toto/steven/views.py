"""Ask, poll, the user's console, and the operator's management page.

Nothing here decides anything — ``services`` holds every judgement. These
translate HTTP into a run and back.

**Ask is a POST that creates a row and returns immediately.** The answer arrives
by polling ``run_status``, which returns the same payload shape fileservices and
texlab return, so the platform has one polling idiom rather than four.

**The management page is 403 for a non-operator, not a redirect** — the same
rule and the same reasoning as ``jess/views.py``: a copy-pasted
``user_passes_test`` sends 302 to LOGIN_URL, which a JSON caller follows into an
HTML login page and cannot parse.
"""

from __future__ import annotations

import json

from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.debug import sensitive_post_parameters
from django.utils.translation import gettext
from django.views.decorators.http import require_GET, require_POST

from toto.ui import PageProcessor

from . import dispatch, services, stats
from .forms import IdentityForm, PersonalizationForm, PromptForm, ProviderForm
from .models import AiAgent, AiPersonalization, AiProvider, AiRun
from .surfaces import DOCUMENT_ACTION, ELEMENT_ACTION, registry, resolve_action

#: The largest selection that may be sent. A selection is not a document — the
#: whole billing model rests on that — and 20k characters is already several
#: pages.
MAX_SELECTION = 20_000

#: The largest DOCUMENT that may be rewritten whole. Bigger than a selection
#: because the whole point is the whole file, and small enough that one press
#: cannot cost a fortune — the wallet check below reserves the worst case
#: before anything runs. Matches the file-level wand's own cap.
MAX_DOCUMENT = 60_000


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
    # resolve_action, not surface.action: the whole-document rewrite is
    # synthesised from the surface's own file type rather than declared by each
    # editor, and the worker resolves it exactly the same way.
    action = resolve_action(surface, action_key)
    if action is None:
        return JsonResponse({"error": "Unknown action."}, status=400)

    whole_document = action_key == DOCUMENT_ACTION
    element = action_key == ELEMENT_ACTION
    # An element generation sends the document as CONTEXT, so it shares the
    # document limit — and it tolerates an empty one, because an empty document
    # is exactly where the first element gets generated.
    limit = MAX_DOCUMENT if (whole_document or element) else MAX_SELECTION

    if not selection.strip() and not element:
        return JsonResponse(
            {"error": "There is nothing here to work on." if whole_document
                      else "Select something first."}, status=400)
    if len(selection) > limit:
        return JsonResponse(
            {"error": f"This is too long ({len(selection)} characters; the "
                      f"limit is {limit}). "
                      + ("Try a smaller file." if whole_document
                         else "Select less, or rewrite the whole document.")},
            status=400)
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
    mine = AiRun.objects.filter(owner=request.user)

    row = AiPersonalization.objects.filter(user=request.user).first()

    card = rates.rate_card()
    return _render(request, "steven/console.html", {
        "provider": provider,
        "configured": bool(provider and provider.secret_id),
        "agent": AiAgent.current(),
        "is_operator": _is_operator(request.user),
        "surfaces": list(registry.all()),
        # Own rows only — the aggregate lives behind the 403 gate on manage.
        **stats.context_for(mine, staff=False),
        "personalization_form": PersonalizationForm(instance=row),
        "price_request": card.get("ai.request"),
        "price_tokens": card.get("ai.tokens_1k"),
        "spend": (rates.spend_by_metric(request.user) or {}),
        "balance": rates.balance_of(request.user),
    })


@login_required
@require_POST
def personalization(request):
    """Save the user's standing note. Console-only; every user has one."""
    row, _created = AiPersonalization.objects.get_or_create(user=request.user)
    form = PersonalizationForm(request.POST, instance=row)
    if form.is_valid():
        form.save()
        django_messages.success(
            request, gettext("Saved. It is sent with every question you ask."))
    else:
        django_messages.error(
            request, "; ".join(e for errors in form.errors.values()
                               for e in errors))
    return redirect("steven:console")


# ---------------------------------------------------------------------------
# Management — two tabs: who it is, and how it writes
# ---------------------------------------------------------------------------

def _is_operator(user) -> bool:
    """``is_superuser`` does not imply ``is_staff`` in Django. Both count."""
    return bool(user.is_authenticated and (user.is_staff or user.is_superuser))


def _operator_only(request):
    if not _is_operator(request.user):
        raise PermissionDenied


def _registered_kinds() -> list:
    """``(kind, where it applies)`` for the surfaces this host actually has.

    Read from the registry rather than a constant, so a host gets note fields
    for the editors it installed and none for the ones it did not — and each
    field says which editors it will reach, because "code" means the ACE editor
    on one host and a notebook cell on another.
    """
    where: dict = {}
    for surface in registry.all():
        if surface.kind:
            where.setdefault(surface.kind, []).append(surface.label)
    return [(kind, ", ".join(labels)) for kind, labels in sorted(where.items())]


def _preview(agent: AiAgent):
    """The system message as the model will receive it, per registered kind.

    This is the point of the second tab. Persona, language, note and house rules
    are assembled in an order with a reason behind it, and an operator who
    cannot see the assembled result is tuning a prompt blindfolded — which is
    how the parked app ended up with a system prompt nobody had read in months.

    One representative action per kind: the preview is about the operator's own
    text and where it lands relative to the action's rule, and showing the same
    envelope six times per surface would bury that.
    """
    from .surfaces import compose_system

    voice = agent.as_voice() if agent else None
    seen, rows = set(), []
    for surface in registry.all():
        if not surface.kind or surface.kind in seen or not surface.actions:
            continue
        seen.add(surface.kind)
        action = surface.actions[0]
        rows.append({
            "kind": surface.kind,
            "surface": surface.label,
            "action": action.label,
            "system": compose_system(surface, action, voice),
            "action_rule": action.system,
        })
    return rows


#: The hub's tabs. Anything else — including the legacy identity/prompt links,
#: which now live on each agent's own page — falls back to the first.
MANAGE_TABS = ("connection", "agents", "statistics")


def manage(request):
    """Steven AI Settings — Connection, Agents, Statistics. The operator's desk.

    Deliberately not the Django admin: the admin edits rows; this page answers
    an operator's three questions — what is answering (and with which key,
    without ever showing it), who it is, and what it has been doing.

    A hub only: no POST lands here. Providers and agents are edited on their
    own pages, so "which row" is a URL rather than a hidden input, and a
    validation error comes back on the page that caused it.
    """
    _operator_only(request)

    tab = request.GET.get("tab") or ""
    if tab not in MANAGE_TABS:
        tab = MANAGE_TABS[0]

    context = {
        "tab": tab,
        "console_url": reverse("steven:console"),
        "configured": False,
    }
    provider = AiProvider.current()
    context["provider"] = provider
    context["configured"] = bool(provider and provider.secret_id)

    if tab == "connection":
        context["providers"] = list(AiProvider.objects.all())
    elif tab == "agents":
        context["agents"] = list(AiAgent.objects.all())
    else:
        context.update(stats.context_for(AiRun.objects.all(), staff=True))

    return _render(request, "steven/manage.html", context)


# The write-only key never reaches an error report either: this is the same
# decorator jess's provider_secret page carries, and it is what keeps a POST
# body out of Django's debug/error emails.
@sensitive_post_parameters("new_api_key")
def provider_edit(request, pk: int | None = None):
    """One provider row: settings, and the write-only key field.

    The key sequence is ``services.store_api_key`` — the admin's exact
    store → repoint → retire → audit, shared so the two doors cannot drift.
    The row is saved FIRST (the sequence needs a pk), then the key; a vault
    failure therefore keeps the row edits and says the key was NOT changed.
    """
    _operator_only(request)
    from .vault import VaultUnavailable

    provider = get_object_or_404(AiProvider, pk=pk) if pk else None
    form = ProviderForm(instance=provider)

    if request.method == "POST":
        form = ProviderForm(request.POST, instance=provider)
        if form.is_valid():
            saved = form.save()
            new_value = (form.cleaned_data.get("new_api_key") or "").strip()
            if new_value:
                try:
                    services.store_api_key(saved, new_value, actor=request.user)
                    django_messages.success(
                        request, gettext("API key stored, encrypted. It will "
                                         "not be shown again."))
                except VaultUnavailable as exc:
                    django_messages.error(
                        request, f"Vault unavailable — the key was NOT "
                                 f"changed: {exc}")
                except Exception as exc:  # noqa: BLE001 — never echo the value
                    django_messages.error(
                        request, f"Could not store the key: {exc}")
            else:
                django_messages.success(request, gettext("Saved."))
            return redirect(f"{reverse('steven:manage')}?tab=connection")

    return _render(request, "steven/provider_form.html", {
        "form": form,
        "provider": provider,
        "key_status": _secret_status(provider),
    })


def _secret_status(provider) -> str:
    """"set · active · rotated 2026-03-01" or "— none". Never the key."""
    secret = getattr(provider, "secret", None) if provider else None
    if not secret:
        return "— none"
    rotated = (f" · rotated {secret.rotated_at:%Y-%m-%d}"
               if secret.rotated_at else "")
    return f"set · {secret.state}{rotated}"


@require_POST
def provider_activate(request, pk: int):
    _operator_only(request)
    provider = get_object_or_404(AiProvider, pk=pk)
    provider.active = True
    provider.save()  # the model save keeps the one-active-row invariant
    django_messages.success(request, f"{provider.label} is now answering.")
    return redirect(f"{reverse('steven:manage')}?tab=connection")


@require_POST
def provider_test(request, pk: int):
    """Prove THIS row, synchronously — prove-then-switch, like the admin action.

    Every message here is formatted from ``probe_provider``'s dict or from an
    exception that cannot contain the key; the probe truncates the answer to
    40 characters itself, so no caller can leak more.
    """
    _operator_only(request)
    from .client import ProviderError
    from .vault import VaultUnavailable

    provider = get_object_or_404(AiProvider, pk=pk)
    try:
        probe = services.probe_provider(provider)
        django_messages.success(
            request,
            f"{provider.label} answered \"{probe['text']}\" as "
            f"{probe['model']} ({probe['tokens']} tokens).")
    except services.NotConfigured as exc:
        django_messages.error(request, str(exc))
    except VaultUnavailable as exc:
        django_messages.error(request, f"Vault: {exc}")
    except ProviderError as exc:
        django_messages.error(request, f"{provider.label} did not answer: {exc}")
    return redirect(f"{reverse('steven:manage')}?tab=connection")


def agent_edit(request, pk: int | None = None):
    """One agent: identity and prompt engineering, two sub-tabs.

    The old single-agent manage page, instance-bound: the assembled-message
    preview beside the boxes that build it, each tab POSTing alone. A NEW
    agent saves inactive — with a list, activation is the list's explicit
    button, not a side effect of typing a persona.
    """
    _operator_only(request)

    agent = get_object_or_404(AiAgent, pk=pk) if pk else AiAgent()
    kinds = _registered_kinds()
    tab = request.GET.get("tab") or "identity"

    identity = IdentityForm(instance=agent)
    prompt = PromptForm(instance=agent, kinds=kinds)

    if request.method == "POST":
        which = request.POST.get("form") or "identity"
        if which == "prompt":
            tab = "prompt"
            prompt = PromptForm(request.POST, instance=agent, kinds=kinds)
            if prompt.is_valid():
                saved = prompt.save()
                django_messages.success(
                    request, gettext("Saved. New questions use it."))
                return redirect(
                    f"{reverse('steven:agent_edit', args=[saved.pk])}?tab=prompt")
        else:
            tab = "identity"
            identity = IdentityForm(request.POST, instance=agent)
            if identity.is_valid():
                saved = identity.save()
                django_messages.success(request, gettext("Saved."))
                return redirect(
                    f"{reverse('steven:agent_edit', args=[saved.pk])}?tab=identity")

    return _render(request, "steven/agent_form.html", {
        "agent": agent if agent.pk else None,
        "identity_form": identity,
        "prompt_form": prompt,
        "tab": tab,
        "kinds": kinds,
        "preview": _preview(agent if agent.pk else None),
    })


@require_POST
def agent_activate(request, pk: int):
    _operator_only(request)
    agent = get_object_or_404(AiAgent, pk=pk)
    agent.active = True
    agent.save()  # the model save keeps the one-active-row invariant
    django_messages.success(request, f"{agent.name} is now the voice.")
    return redirect(f"{reverse('steven:manage')}?tab=agents")


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
    from toto.core import assistant

    if not assistant.allowed_for_file(vault_file):
        # The bucket's AI shield. Refused HERE, not merely unlisted: the wand
        # plugin hides the menu entry, but a URL somebody kept must refuse too.
        raise Http404("This bucket is protected from the assistant.")

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
    # The agent rides along on a request the toolbar already makes, so its name
    # reaches six editor panels without threading a context variable through
    # six templates in three packages. Absent when nobody configured one, and
    # the panel falls back to calling itself "Assistant".
    agent = AiAgent.current()
    from .surfaces import LANGUAGE_NAMES

    return JsonResponse({
        "surface": surface.key,
        "label": surface.label,
        # What a whole-document rewrite will be asked to emit. Shown in the
        # modal so somebody pressing AI on an .html file can see it is about to
        # get HTML back rather than prose about their HTML.
        "language": LANGUAGE_NAMES.get(surface.file_type, surface.file_type),
        "agent": ({"name": agent.name, "icon": agent.icon,
                   "tagline": agent.tagline} if agent else None),
        "actions": [
            {"key": a.key, "label": str(a.label), "icon": a.icon,
             "needs_instruction": a.needs_instruction,
             "placeholder": str(a.instruction_placeholder)}
            for a in surface.actions
        ],
    })
