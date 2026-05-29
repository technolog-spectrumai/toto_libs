from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

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


@require_POST
def quick_ask(request, slug):
    agent = get_object_or_404(AgentProfile, slug=slug, is_active=True)
    user_prompt = request.POST.get("user_prompt", "").strip()

    if not user_prompt:
        return HttpResponse('<span class="opacity-50 italic">No prompt provided.</span>')

    # ── Tariff balance check before AI run ───────────────────────────────────
    try:
        from toto.tariffs.charge import (
            InsufficientBalanceError, check_user_can_act, get_tariff_for_user,
        )
        _steven_tariff = get_tariff_for_user(request.user, "steven")
        if _steven_tariff:
            check_user_can_act(request.user, _steven_tariff, "ai.agent_run", 1)
    except InsufficientBalanceError as _exc:
        return HttpResponse(
            f'<span class="opacity-70"><i class="fa-solid fa-circle-xmark mr-1 text-red-500"></i>'
            f'Insufficient balance: {_exc}</span>',
            status=402,
        )
    except Exception:
        _steven_tariff = None

    agent_run = AgentRun(agent=agent, user_prompt=user_prompt)
    agent_run.save()
    create_agent_session(agent).run(agent_run)

    # ── Charge after run ─────────────────────────────────────────────────────
    try:
        if _steven_tariff:
            from toto.tariffs.charge import charge_user as _charge
            _charge(request.user, _steven_tariff, "ai.agent_run", 1,
                    source_type="steven.AgentRun", source_id=str(agent_run.pk))
    except Exception:
        pass

    if agent_run.status == "failed":
        return HttpResponse(
            f'<span class="text-red-500"><i class="fa-solid fa-circle-exclamation mr-1"></i>{agent_run.error}</span>'
        )

    return HttpResponse(agent_run.result)