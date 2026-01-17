from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.db.models import Count
from oya.page import PageProcessor
import json

from .models import Question, Choice, Answer
from django.shortcuts import render

@login_required
def poll_list(request):
    """
    Card-style list of all active polls (Decks-style layout).
    """
    polls = (
        Question.objects.filter(is_active=True)
        .prefetch_related("choices")
        .order_by("-pub_date")
    )

    # Transform into a simple list for the template
    poll_items = []
    for poll in polls:
        poll_items.append({
            "title": poll.name,
            "question": poll.question_text,
            "choices": ", ".join([c.label for c in poll.choices.all()]),
            "published": poll.pub_date.strftime("%Y-%m-%d"),
            "url": reverse("polls:poll_detail", args=[poll.pk]),
        })

    context = {
        "polls": poll_items,
    }

    return render(
        request,
        "polls/poll_list.html",
        PageProcessor().decorate(context, request)
    )

@login_required
def poll_detail(request, pk):
    """
    Show poll details and answer form (NO results here).
    """
    poll = get_object_or_404(
        Question.objects.prefetch_related("choices"),
        pk=pk,
        is_active=True,
    )

    # Handle answer submission
    if request.method == "POST":
        choice_id = request.POST.get("choice")
        if choice_id:
            choice = get_object_or_404(Choice, pk=choice_id, question=poll)

            Answer.objects.update_or_create(
                user=request.user,
                question=poll,
                defaults={"choice": choice},
            )

            return redirect("polls:poll_detail", pk=poll.pk)

    choices = poll.choices.all().order_by("label")

    user_answer = Answer.objects.filter(
        user=request.user,
        question=poll
    ).first()

    context = {
        "poll": poll,
        "choices": choices,
        "user_answer": user_answer,
    }

    return render(
        request,
        "polls/poll_detail.html",
        PageProcessor().decorate(context, request)
    )


@login_required
def poll_results(request, pk):
    poll = get_object_or_404(
        Question.objects.prefetch_related("choices", "answers__choice"),
        pk=pk,
    )

    choices = poll.choices.all().order_by("label")

    results = []
    for c in choices:
        count = poll.answers.filter(choice=c).count()
        results.append({
            "label": c.label,
            "text": c.choice_text,
            "value": c.value,
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


