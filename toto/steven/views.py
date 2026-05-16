from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from .forms import AgentRunForm
from .models import AgentProfile, AgentRun
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


@require_POST
def quick_ask(request, slug):
    agent = get_object_or_404(AgentProfile, slug=slug, is_active=True)
    user_prompt = request.POST.get("user_prompt", "").strip()

    if not user_prompt:
        return HttpResponse('<span class="opacity-50 italic">No prompt provided.</span>')

    agent_run = AgentRun(agent=agent, user_prompt=user_prompt)
    agent_run.save()
    create_agent_session(agent).run(agent_run)

    if agent_run.status == "failed":
        return HttpResponse(
            f'<span class="text-red-500"><i class="fa-solid fa-circle-exclamation mr-1"></i>{agent_run.error}</span>'
        )

    return HttpResponse(agent_run.result)