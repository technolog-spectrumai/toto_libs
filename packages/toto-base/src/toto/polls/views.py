"""One tab over one engine: a question, its options, and the count.

There were two tabs until 1.50 — Polls and Votes — over the same mechanism,
differing in what the question itself declared: a poll was revisable and showed
a live tally, a formal vote was cast once, froze a roll, met a quorum, cleared
a consensus threshold and wrote an immutable chain-linked Decision.

The second tab has gone, with ~20 of this module's 30 views: the ledger, its
PDF export, the checkpoint pages, the snapshot pages, the electorate pages, the
paper-result recorder and the vote creator. Formal company governance is
Irena's — it is the register of record — and Ireneo presents verified copies of
it inside Zenobia. Nothing here produces a legally or institutionally binding
outcome, and there is no longer any page that could suggest otherwise.

What is left: question -> options -> members' responses -> informational
result. Only platform-wide questions appear here; one scoped to a Forum room
belongs to that room and is listed there, which is why every query goes through
``in_scope()`` with the global scope rather than filtering by hand.
"""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from . import services
from .core import Revisability, Visibility, VotingError
from .models import SCOPE_GLOBAL, Choice, Kind, Question

#: The chart palette. One list, so every question of the same shape looks like
#: the same object.
PALETTE = ["#4F46E5", "#10B981", "#F59E0B", "#EF4444", "#3B82F6", "#8B5CF6"]


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _is_operator(user) -> bool:
    return user.is_staff or user.is_superuser


def _global():
    return (Question.objects.in_scope(SCOPE_GLOBAL)
            .filter(kind=Kind.POLL)
            .prefetch_related("choices"))


def _card(question) -> dict:
    return {
        "question": question,
        "options": ", ".join(c.label for c in question.choices.all()),
        "is_open": question.is_open,
        "url": reverse("polls:question_detail",
                       args=[question.kind, question.slug]),
    }


@login_required
def poll_list(request):
    """Every platform-wide consultation."""
    return _render(request, "polls/question_list.html", {
        "active_tab": "polls",
        "kind": Kind.POLL,
        "cards": [_card(q) for q in _global()],
        "is_operator": _is_operator(request.user),
    })


@login_required
def question_detail(request, kind, slug):
    """One question, its options and where the reader stands with it."""
    question = get_object_or_404(
        Question.objects.in_scope(SCOPE_GLOBAL).prefetch_related("choices"),
        kind=kind, slug=slug)

    roll = services.electorate_for(question)
    standing = services.standing(question, request.user, electorate=roll)
    ballot = services.ballot_of(question, request.user)
    visible = services.may_see_results(question, request.user, electorate=roll)

    return _render(request, "polls/question_detail.html", {
        "active_tab": "polls",
        "question": question,
        "choices": question.choices.all(),
        "ballot": ballot,
        "standing": standing,
        "results_visible": visible,
        "tally": (services.tally(question, electorate=roll)
                  if visible else None),
        "is_final": question.revisability == Revisability.FINAL,
        "results_url": reverse("polls:question_results",
                               args=[question.kind, question.slug]),
        "is_operator": _is_operator(request.user),
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
        services.cast(question, request.user, choice,
                      electorate=services.electorate_for(question))
    except VotingError as exc:
        # Every refusal carries a sentence; showing it is the whole reason the
        # engine raises typed errors instead of returning False.
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Your answer has been recorded."))

    return redirect(reverse("polls:question_detail",
                            args=[question.kind, question.slug]))


@login_required
def question_results(request, kind, slug):
    """The count, when the question's own rules allow it to be read."""
    question = get_object_or_404(
        Question.objects.in_scope(SCOPE_GLOBAL).prefetch_related("choices"),
        kind=kind, slug=slug)

    roll = services.electorate_for(question)
    if not services.may_see_results(question, request.user, electorate=roll):
        return _render(request, "polls/results_withheld.html", {
            "active_tab": "polls",
            "question": question,
            "opens_when_closed": question.visibility != Visibility.LIVE,
        })

    counted = services.tally(question, electorate=roll)
    labels = [r.label for r in counted.results]
    weights = [r.weight for r in counted.results]

    return _render(request, "polls/question_results.html", {
        "active_tab": "polls",
        "question": question,
        "tally": counted,
        "turnout_percent": (round(counted.turnout * 100)
                            if counted.turnout is not None else None),
        "rows": [
            {"result": r,
             "share_percent": (counted.share(r) * 100
                               if counted.share(r) is not None else None)}
            for r in counted.results
        ],
        "pie_chart_json": json.dumps({
            "chart_type": "pie",
            "labels": labels,
            "datasets": [{"data": weights, "backgroundColor": PALETTE}],
        }),
        "bar_chart_json": json.dumps({
            "chart_type": "bar",
            "labels": labels,
            "datasets": [{"label": "Responses", "data": weights,
                          "backgroundColor": PALETTE}],
            "options": {"scales": {"y": {"beginAtZero": True}}},
        }),
    })


@login_required
@require_POST
def question_close(request, kind, slug):
    """Stop accepting answers. Staff's act, and the end of it.

    Closing used to mean recording a Decision: freezing the count into an
    immutable, chain-linked row that a quorum and a consensus rule had been
    evaluated against. It now means what the word says — no more answers — and
    the result stays what it always was here, which is information.
    """
    question = get_object_or_404(
        Question.objects.in_scope(SCOPE_GLOBAL), kind=kind, slug=slug)
    if not _is_operator(request.user):
        raise PermissionDenied(_("Only staff close a consultation."))

    question.close()          # idempotent, and the model owns the rule
    messages.success(request, _("The consultation is closed."))
    return redirect(reverse("polls:question_results",
                            args=[question.kind, question.slug]))
