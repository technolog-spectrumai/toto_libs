from decimal import Decimal

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from toto.ui import PageProcessor

from .forms import AgentRunForm
from .models import AgentProfile, AgentRun, ChatMessage, Conversation
from toto.steven.services.agent_session import create_agent_session


def render_steven(request, template_name, context):
    return render(
        request,
        template_name,
        PageProcessor().decorate(context, request),
    )


def agent_list(request):
    agents = AgentProfile.objects.filter(
        is_active=True,
    ).prefetch_related("tools")

    recent_runs = AgentRun.objects.select_related("agent")[:10]

    return render_steven(
        request,
        "steven/agent_list.html",
        {
            "agents": agents,
            "recent_runs": recent_runs,
        },
    )


def agent_detail(request, slug):
    agent = get_object_or_404(
        AgentProfile.objects.select_related("connector").prefetch_related("tools"),
        slug=slug,
        is_active=True,
    )

    if request.method == "POST":
        form = AgentRunForm(request.POST)

        if form.is_valid():
            agent_run = form.save(commit=False)
            agent_run.agent = agent
            agent_run.save()

            create_agent_session(agent).run(agent_run)

            if agent_run.status == "failed":
                messages.error(
                    request,
                    "Steven could not complete the run. Check the error details below.",
                )

            return redirect("steven:run_detail", pk=agent_run.pk)

    else:
        form = AgentRunForm()

    runs = agent.runs.all()[:20]

    return render_steven(
        request,
        "steven/agent_detail.html",
        {
            "agent": agent,
            "form": form,
            "runs": runs,
        },
    )


def run_detail(request, pk):
    agent_run = get_object_or_404(
        AgentRun.objects.select_related("agent"),
        pk=pk,
    )

    return render_steven(
        request,
        "steven/run_detail.html",
        {
            "run": agent_run,
        },
    )


def conversation_new(request, slug):
    agent = get_object_or_404(AgentProfile, slug=slug, is_active=True)

    if request.method != "POST":
        return redirect("steven:agent_detail", slug=slug)

    user_prompt = request.POST.get("user_prompt", "").strip()
    if not user_prompt:
        return redirect("steven:agent_detail", slug=slug)

    conversation = Conversation.objects.create(
        agent=agent,
        title=user_prompt[:80],
    )
    ChatMessage.objects.create(conversation=conversation, role=ChatMessage.ROLE_USER, content=user_prompt)

    session = create_agent_session(agent)
    try:
        result = session.invoke(user_prompt)
    except Exception as exc:
        result = f"[Error] {exc}"

    ChatMessage.objects.create(conversation=conversation, role=ChatMessage.ROLE_ASSISTANT, content=result)
    return redirect("steven:conversation_detail", slug=slug, pk=conversation.pk)


def conversation_detail(request, slug, pk):
    agent = get_object_or_404(AgentProfile, slug=slug, is_active=True)
    conversation = get_object_or_404(Conversation, pk=pk, agent=agent)

    if request.method == "POST":
        user_prompt = request.POST.get("user_prompt", "").strip()
        if user_prompt:
            history = [
                {"role": msg.role, "content": msg.content}
                for msg in conversation.messages.all()
            ]
            ChatMessage.objects.create(
                conversation=conversation, role=ChatMessage.ROLE_USER, content=user_prompt
            )
            session = create_agent_session(agent)
            try:
                result = session.invoke(user_prompt, history=history)
            except Exception as exc:
                result = f"[Error] {exc}"
            ChatMessage.objects.create(
                conversation=conversation, role=ChatMessage.ROLE_ASSISTANT, content=result
            )
            conversation.save()
        return redirect("steven:conversation_detail", slug=slug, pk=pk)

    return render_steven(
        request,
        "steven/chat.html",
        {
            "agent": agent,
            "conversation": conversation,
            "chat_messages": conversation.messages.all(),
        },
    )


@require_GET
def estimate_cost(request, slug):
    """Return estimated BANANA cost for a prompt of *chars* characters. Used by debounce JS."""
    try:
        chars = max(0, int(request.GET.get("chars", 0)))
    except (TypeError, ValueError):
        chars = 0

    # ~4 chars per token (rough universal heuristic)
    estimated_tokens = max(1, round(chars / 4))

    try:
        from toto.metering.charge import get_tariff_for_user

        if not request.user.is_authenticated:
            return JsonResponse({"estimate": None})

        tariff = get_tariff_for_user(request.user, "steven")
        if not tariff:
            return JsonResponse({"estimate": None})

        # Collect all active items for ai.requests + ai.input_tokens regardless of token type.
        # Group by charged_asset so we return cost in whichever token the tariff uses.
        items = list(
            tariff.active_items
                .select_related("metric", "charged_asset")
                .filter(metric__code__in=["ai.requests", "ai.input_tokens"])
        )
        if not items:
            return JsonResponse({"estimate": None})

        # Pick the asset used by the input-tokens item; fall back to request item.
        inp_item = next((i for i in items if i.metric.code == "ai.input_tokens"), None)
        req_item = next((i for i in items if i.metric.code == "ai.requests"), None)
        ref_item = inp_item or req_item
        token_asset = ref_item.charged_asset

        cost = Decimal("0")
        if req_item and req_item.charged_asset_id == token_asset.pk:
            cost += req_item.price_per_unit_display
        if inp_item and inp_item.charged_asset_id == token_asset.pk:
            uq = inp_item.unit_quantity or Decimal("1000")
            cost += (Decimal(estimated_tokens) / uq) * inp_item.price_per_unit_display

        if cost == 0:
            return JsonResponse({"estimate": None})

        unit_name = token_asset.unit_name if token_asset else "tokens"
        return JsonResponse({
            "estimate": str(cost.quantize(Decimal("0.001"))),
            "unit": unit_name,
            "tokens": estimated_tokens,
        })
    except Exception:
        return JsonResponse({"estimate": None})


@require_POST
def quick_ask(request, slug):
    agent = get_object_or_404(AgentProfile, slug=slug, is_active=True)
    user_prompt = request.POST.get("user_prompt", "").strip()

    if not user_prompt:
        return HttpResponse('<span class="opacity-50 italic">No prompt provided.</span>')

    # ── Tariff balance check before AI run ───────────────────────────────────
    from toto.metering.charge import (
        InsufficientBalanceError, check_user_can_act, get_tariff_for_user,
    )
    _steven_tariff = get_tariff_for_user(request.user, "steven")
    if _steven_tariff:
        try:
            check_user_can_act(request.user, _steven_tariff, "ai.agent_run", 1)
        except InsufficientBalanceError as _exc:
            return HttpResponse(
                f'<span class="opacity-70"><i class="fa-solid fa-circle-xmark mr-1 text-red-500"></i>'
                f'Insufficient balance: {_exc}</span>',
                status=402,
            )

    agent_run = AgentRun(agent=agent, user_prompt=user_prompt)
    agent_run.save()
    create_agent_session(agent).run(agent_run)

    # ── Charge after run ─────────────────────────────────────────────────────
    if _steven_tariff:
        from toto.metering.charge import charge_user as _charge
        try:
            _charge(request.user, _steven_tariff, "ai.agent_run", 1,
                    source_type="steven.AgentRun", source_id=str(agent_run.pk))
        except Exception:
            pass

    if agent_run.status == "failed":
        return HttpResponse(
            f'<span class="text-red-500"><i class="fa-solid fa-circle-exclamation mr-1"></i>{agent_run.error}</span>'
        )

    return HttpResponse(agent_run.result)