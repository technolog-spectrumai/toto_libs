from decimal import Decimal

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from toto.ui import PageProcessor

from .forms import AgentRunForm
from .models import AgentProfile, AgentRun, ChatMessage, Conversation
from toto.steven.services.agent_session import create_agent_session


def _connector_is_slow(agent) -> bool:
    """True when inference should go through Celery/workflow (the default)."""
    connector = getattr(agent, "connector", None)
    if connector is None:
        return True
    return bool(getattr(connector, "is_slow", True))


def _queue_via_workflow(agent_run_pk: int) -> bool:
    """Push agent_run_pk into the 'steven-run-agent' workflow.

    Returns True when queued. Returns False (fallback to sync) when Celery
    is unavailable or the workflow hasn't been seeded yet.
    """
    try:
        from toto.workflows.models import Workflow, WorkflowRun
        from toto.workflows.tasks import start_workflow_run_task
        from toto.celery_utils import celery_available

        if not celery_available():
            return False

        wf = Workflow.objects.filter(slug="steven-run-agent").first()
        if wf is None:
            return False

        run = WorkflowRun.objects.create(
            workflow=wf,
            input_data={"data": {"agent_run_pk": agent_run_pk}},
        )
        start_workflow_run_task.delay(run.pk)
        return True
    except Exception:
        return False


def _run_agent(agent, agent_run) -> bool:
    """Execute *agent_run* — async via Celery when the connector is slow, sync otherwise.

    Returns True when queued asynchronously, False when executed synchronously.
    """
    if _connector_is_slow(agent) and _queue_via_workflow(agent_run.pk):
        return True
    create_agent_session(agent).run(agent_run)
    return False


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

    from toto.quota import usage_summary
    quota_data = usage_summary("steven", "auth.User", str(request.user.pk)) if request.user.is_authenticated else []

    return render_steven(
        request,
        "steven/agent_list.html",
        {
            "agents": agents,
            "recent_runs": recent_runs,
            "quota_data": quota_data,
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

            queued = _run_agent(agent, agent_run)
            if not queued and agent_run.status == "failed":
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
            ChatMessage.objects.create(
                conversation=conversation, role=ChatMessage.ROLE_USER, content=user_prompt
            )

            if _connector_is_slow(agent):
                agent_run = AgentRun.objects.create(
                    agent=agent, user_prompt=user_prompt
                )
                queued = _queue_via_workflow(agent_run.pk)
                if not queued:
                    create_agent_session(agent).run(agent_run)
                result = agent_run.result if agent_run.status == "succeeded" else (
                    f"[Error] {agent_run.error}" if agent_run.status == "failed"
                    else "[Queued — refresh to see the response]"
                )
            else:
                history = [
                    {"role": msg.role, "content": msg.content}
                    for msg in conversation.messages.exclude(
                        role=ChatMessage.ROLE_USER,
                        content=user_prompt,
                    ).order_by("created_at")
                ]
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

    workflow = None
    try:
        from toto.workflows.models import Workflow
        workflow = Workflow.objects.filter(slug="steven-run-agent").first()
    except Exception:
        pass

    return render_steven(
        request,
        "steven/chat.html",
        {
            "agent": agent,
            "conversation": conversation,
            "chat_messages": conversation.messages.all(),
            "workflow": workflow,
        },
    )


@require_GET
def estimate_cost(request, slug):
    return JsonResponse({"estimate": None})


@require_POST
def quick_ask(request, slug):
    agent = get_object_or_404(AgentProfile, slug=slug, is_active=True)
    user_prompt = request.POST.get("user_prompt", "").strip()

    if not user_prompt:
        return HttpResponse('<span class="opacity-50 italic">No prompt provided.</span>')

    if request.user.is_authenticated:
        from toto.quota import QuotaExceeded, check_quota
        try:
            check_quota("steven", "ai.agent_run", 1, "auth.User", str(request.user.pk))
        except QuotaExceeded as _exc:
            return HttpResponse(
                f'<span class="opacity-70"><i class="fa-solid fa-circle-xmark mr-1 text-red-500"></i>'
                f'{_exc}</span>',
                status=429,
            )

    agent_run = AgentRun(agent=agent, user_prompt=user_prompt)
    agent_run.save()

    if _connector_is_slow(agent) and _queue_via_workflow(agent_run.pk):
        poll_url = f"/steven/runs/{agent_run.pk}/status/"
        return HttpResponse(
            f'<span id="quick-ask-{agent_run.pk}"'
            f' hx-get="{poll_url}"'
            f' hx-trigger="every 1.5s"'
            f' hx-target="this"'
            f' hx-swap="outerHTML">'
            f'<i class="fa-solid fa-spinner fa-spin mr-1 opacity-50"></i>'
            f'<span class="opacity-50 italic">Running…</span>'
            f'</span>'
        )

    create_agent_session(agent).run(agent_run)

    if request.user.is_authenticated:
        from toto.quota import record_usage as _ru
        _uid = str(request.user.pk)
        _src = {"source_type": "steven.AgentRun", "source_id": str(agent_run.pk)}
        _ru("steven", "ai.agent_run", 1, "auth.User", _uid,
            idempotency_key=f"steven.agent_run:{agent_run.pk}", **_src)

    if agent_run.status == "failed":
        return HttpResponse(
            f'<span class="text-red-500"><i class="fa-solid fa-circle-exclamation mr-1"></i>{agent_run.error}</span>'
        )

    return HttpResponse(agent_run.result)


@require_GET
def run_status(request, pk):
    """HTMX polling endpoint: returns a fragment while running, the result when done."""
    run = get_object_or_404(AgentRun.objects.only("status", "result", "error"), pk=pk)

    if run.status in ("queued", "running"):
        poll_url = f"/steven/runs/{pk}/status/"
        return HttpResponse(
            f'<span id="quick-ask-{pk}"'
            f' hx-get="{poll_url}"'
            f' hx-trigger="every 1.5s"'
            f' hx-target="this"'
            f' hx-swap="outerHTML">'
            f'<i class="fa-solid fa-spinner fa-spin mr-1 opacity-50"></i>'
            f'<span class="opacity-50 italic">Running…</span>'
            f'</span>'
        )

    if run.status == "failed":
        return HttpResponse(
            f'<span class="text-red-500">'
            f'<i class="fa-solid fa-circle-exclamation mr-1"></i>{run.error}'
            f'</span>'
        )


    return HttpResponse(run.result or "")