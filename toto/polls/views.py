from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from toto.core.page import PageProcessor
import json

from .models import Poll, Option, Vote


@login_required
def poll_list(request):
    """
    Card-style list of all active polls.
    """
    polls = (
        Poll.objects.filter(is_active=True)
        .prefetch_related("options")
        .order_by("-pub_date")
    )

    poll_items = []
    for poll in polls:
        poll_items.append({
            "title": poll.name,
            "question": poll.question_text,
            "choices": ", ".join([c.label for c in poll.options.all()]),
            "published": poll.pub_date.strftime("%Y-%m-%d"),
            "url": reverse("polls:poll_detail", kwargs={"slug": poll.slug}),
        })

    context = {"polls": poll_items}

    return render(
        request,
        "polls/poll_list.html",
        PageProcessor().decorate(context, request)
    )


@login_required
def poll_detail(request, slug):
    """
    Show poll details and voting form.
    """
    poll = get_object_or_404(
        Poll.objects.prefetch_related("options"),
        slug=slug,
        is_active=True,
    )

    # Handle vote submission
    if request.method == "POST":
        option_id = request.POST.get("option")
        if option_id:
            option = get_object_or_404(Option, pk=option_id, poll=poll)

            Vote.objects.update_or_create(
                user=request.user,
                poll=poll,
                defaults={"option": option},
            )

            return redirect("polls:poll_detail", slug=poll.slug)

    options = poll.options.all().order_by("label")

    user_vote = Vote.objects.filter(
        user=request.user,
        poll=poll
    ).first()

    context = {
        "poll": poll,
        "options": options,
        "user_vote": user_vote,
    }

    return render(
        request,
        "polls/poll_detail.html",
        PageProcessor().decorate(context, request)
    )


@login_required
def poll_results(request, slug):
    poll = get_object_or_404(
        Poll.objects.prefetch_related("options", "votes__option"),
        slug=slug,
    )

    options = poll.options.all().order_by("label")

    results = []
    for opt in options:
        count = poll.votes.filter(option=opt).count()
        results.append({
            "label": opt.label,
            "text": opt.option_text,
            "value": opt.value,
            "votes": count,
        })

    # PIE CHART
    pie_chart = {
        "chart_type": "pie",
        "labels": [r["label"] for r in results],
        "datasets": [{
            "data": [r["votes"] for r in results],
            "backgroundColor": [
                "#4F46E5", "#10B981", "#F59E0B",
                "#EF4444", "#3B82F6", "#8B5CF6"
            ],
        }],
    }

    # BAR CHART
    bar_chart = {
        "chart_type": "bar",
        "labels": [r["label"] for r in results],
        "datasets": [{
            "label": "Votes",
            "data": [r["votes"] for r in results],
            "backgroundColor": [
                "#4F46E5", "#10B981", "#F59E0B",
                "#EF4444", "#3B82F6", "#8B5CF6"
            ],
        }],
        "options": {
            "scales": {
                "y": {"beginAtZero": True}
            }
        }
    }

    context = {
        "poll": poll,
        "results": results,
        "pie_chart_json": json.dumps(pie_chart),
        "bar_chart_json": json.dumps(bar_chart),
    }

    return render(
        request,
        "polls/poll_results.html",
        PageProcessor().decorate(context, request)
    )
