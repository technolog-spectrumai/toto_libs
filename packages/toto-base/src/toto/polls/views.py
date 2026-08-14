"""Two tabs over one engine: Polls and Votes.

They are separate pages with separate rules and deliberately similar shapes,
because they ARE the same mechanism — a question, its options, a deadline and a
count. What differs is stated on the question itself, not branched here:

* a **poll** is revisable while open and shows a live tally;
* a **vote** is cast once and shows nothing until it closes.

Only platform-wide questions appear here. A question scoped to a Forum room or
a company belongs to that room or that company and is listed there — every
query on this page goes through ``in_scope()`` with the global scope, so a
company's votes cannot leak into the public list by omission.
"""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from . import services
from .core import Revisability, Visibility, VotingError
from .models import SCOPE_GLOBAL, Choice, Kind, Question

#: The chart palette, shared by both tabs so a poll and a vote of the same shape
#: look like the same object.
PALETTE = ["#4F46E5", "#10B981", "#F59E0B", "#EF4444", "#3B82F6", "#8B5CF6"]


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _global(kind):
    return (Question.objects.in_scope(SCOPE_GLOBAL)
            .filter(kind=kind)
            .prefetch_related("choices"))


def _card(question) -> dict:
    return {
        "question": question,
        "options": ", ".join(c.label for c in question.choices.all()),
        "is_open": question.is_open,
        "url": f"/polls/{question.kind}/{question.slug}/",
    }


@login_required
def poll_list(request):
    """The lightweight tab: questions anyone may answer."""
    return _render(request, "polls/question_list.html", {
        "active_tab": "polls",
        "kind": Kind.POLL,
        "cards": [_card(q) for q in _global(Kind.POLL)],
    })


@login_required
def vote_list(request):
    """The formal tab: decisions, with their outcomes once they close."""
    return _render(request, "polls/question_list.html", {
        "active_tab": "votes",
        "kind": Kind.VOTE,
        "cards": [_card(q) for q in _global(Kind.VOTE)],
    })


@login_required
def question_detail(request, kind, slug):
    """One question. The same page for both kinds — the rules differ, not the shape."""
    question = get_object_or_404(
        Question.objects.in_scope(SCOPE_GLOBAL).prefetch_related("choices"),
        kind=kind, slug=slug)

    standing = services.standing(question, request.user)
    ballot = services.ballot_of(question, request.user)
    visible = services.may_see_results(question, request.user)

    return _render(request, "polls/question_detail.html", {
        "active_tab": "votes" if question.is_formal else "polls",
        "question": question,
        "choices": question.choices.all(),
        "ballot": ballot,
        "standing": standing,
        "results_visible": visible,
        "tally": services.tally(question) if visible else None,
        # A formal ballot cannot be changed; say so before somebody clicks.
        "is_final": question.revisability == Revisability.FINAL,
        "results_url": f"/polls/{question.kind}/{question.slug}/results/",
    })


@login_required
@require_POST
def question_vote(request, kind, slug):
    """Record one answer, then send the reader back to the question."""
    question = get_object_or_404(
        Question.objects.in_scope(SCOPE_GLOBAL), kind=kind, slug=slug)
    choice = get_object_or_404(Choice, pk=request.POST.get("choice") or 0,
                               question=question)

    try:
        services.cast(question, request.user, choice)
    except VotingError as exc:
        # Every refusal carries a sentence; showing it is the whole reason the
        # engine raises typed errors instead of returning False.
        messages.error(request, str(exc))
    else:
        messages.success(request, "Your answer has been recorded.")

    return redirect(f"/polls/{question.kind}/{question.slug}/")


@login_required
def question_results(request, kind, slug):
    """The count, when the question's own rules allow it to be read."""
    question = get_object_or_404(
        Question.objects.in_scope(SCOPE_GLOBAL).prefetch_related("choices"),
        kind=kind, slug=slug)

    if not services.may_see_results(question, request.user):
        return _render(request, "polls/results_withheld.html", {
            "active_tab": "votes" if question.is_formal else "polls",
            "question": question,
            "opens_when_closed": question.visibility != Visibility.LIVE,
        })

    counted = services.tally(question)
    labels = [r.label for r in counted.results]
    weights = [r.weight for r in counted.results]

    return _render(request, "polls/question_results.html", {
        "active_tab": "votes" if question.is_formal else "polls",
        "question": question,
        "tally": counted,
        "rows": [
            {"result": r, "share": counted.share(r)} for r in counted.results
        ],
        "pie_chart_json": json.dumps({
            "chart_type": "pie",
            "labels": labels,
            "datasets": [{"data": weights, "backgroundColor": PALETTE}],
        }),
        "bar_chart_json": json.dumps({
            "chart_type": "bar",
            "labels": labels,
            "datasets": [{"label": "Weight", "data": weights,
                          "backgroundColor": PALETTE}],
            "options": {"scales": {"y": {"beginAtZero": True}}},
        }),
    })
