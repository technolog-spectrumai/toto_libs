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
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from . import render_pdf, services
from .core import Revisability, Visibility, VotingError
from .forms import VoteCreateForm
from .models import (SCOPE_GLOBAL, Choice, Decision, Kind, Outcome,
                     PollsQuotaPolicy, PollsUsageEvent, Question)

#: The chart palette, shared by both tabs so a poll and a vote of the same shape
#: look like the same object.
PALETTE = ["#4F46E5", "#10B981", "#F59E0B", "#EF4444", "#3B82F6", "#8B5CF6"]

LEDGER_PAGE_SIZE = 25


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _is_operator(user) -> bool:
    return user.is_staff or user.is_superuser


def _global(kind):
    qs = (Question.objects.in_scope(SCOPE_GLOBAL)
          .filter(kind=kind)
          .prefetch_related("choices"))
    if kind == Kind.VOTE:
        qs = qs.select_related("decision")
    return qs


def _card(question) -> dict:
    decision = getattr(question, "decision", None) if question.is_formal \
        else None
    return {
        "question": question,
        "options": ", ".join(c.label for c in question.choices.all()),
        "is_open": question.is_open,
        "decision": decision,
        "url": reverse("polls:question_detail",
                       args=[question.kind, question.slug]),
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
    # Any vote whose deadline passed since the last visit gets its decision
    # written down before the page could show it undecided.
    services.record_overdue(SCOPE_GLOBAL)
    return _render(request, "polls/question_list.html", {
        "active_tab": "votes",
        "kind": Kind.VOTE,
        "cards": [_card(q) for q in _global(Kind.VOTE)],
        "is_operator": _is_operator(request.user),
    })


@login_required
def question_detail(request, kind, slug):
    """One question. The same page for both kinds — the rules differ, not the shape."""
    question = get_object_or_404(
        Question.objects.in_scope(SCOPE_GLOBAL).prefetch_related("choices"),
        kind=kind, slug=slug)

    roll = services.electorate_for(question)
    standing = services.standing(question, request.user, electorate=roll)
    ballot = services.ballot_of(question, request.user)
    visible = services.may_see_results(question, request.user, electorate=roll)
    decision = (Decision.objects.filter(question=question).first()
                if question.is_formal else None)

    return _render(request, "polls/question_detail.html", {
        "active_tab": "votes" if question.is_formal else "polls",
        "question": question,
        "choices": question.choices.all(),
        "ballot": ballot,
        "standing": standing,
        "results_visible": visible,
        "tally": (services.tally(question, electorate=roll)
                  if visible else None),
        # A formal ballot cannot be changed; say so before somebody clicks.
        "is_final": question.revisability == Revisability.FINAL,
        "results_url": reverse("polls:question_results",
                               args=[question.kind, question.slug]),
        "decision": decision,
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

    if question.is_formal:
        # The page that shows a result must not show an overdue vote undecided.
        services.record_overdue(SCOPE_GLOBAL)
        question.refresh_from_db()

    roll = services.electorate_for(question)
    if not services.may_see_results(question, request.user, electorate=roll):
        return _render(request, "polls/results_withheld.html", {
            "active_tab": "votes" if question.is_formal else "polls",
            "question": question,
            "opens_when_closed": question.visibility != Visibility.LIVE,
        })

    counted = services.tally(question, electorate=roll)
    labels = [r.label for r in counted.results]
    weights = [r.weight for r in counted.results]
    decision = (Decision.objects.filter(question=question).first()
                if question.is_formal else None)

    return _render(request, "polls/question_results.html", {
        "active_tab": "votes" if question.is_formal else "polls",
        "question": question,
        "tally": counted,
        "turnout_percent": (round(counted.turnout * 100)
                            if counted.turnout is not None else None),
        "decision": decision,
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
            "datasets": [{"label": "Weight", "data": weights,
                          "backgroundColor": PALETTE}],
            "options": {"scales": {"y": {"beginAtZero": True}}},
        }),
    })


# -- formal votes: creating, closing, the ledger, the paper -------------------

@login_required
def vote_create(request):
    """A staff form that turns a proposal into a formal vote.

    403 for everyone else, never a redirect: being told a door exists and is
    locked is honest; being bounced to another page is a mystery.
    """
    if not _is_operator(request.user):
        raise PermissionDenied(_("Only staff open a formal vote."))

    form = VoteCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        question = form.save_vote(request.user)
        messages.success(request, _("The vote is open."))
        return redirect(reverse("polls:question_detail",
                                args=[question.kind, question.slug]))

    return _render(request, "polls/vote_form.html", {
        "active_tab": "votes",
        "form": form,
    })


@login_required
@require_POST
def question_close(request, kind, slug):
    """Close a formal vote and write its decision down. Staff's act."""
    question = get_object_or_404(
        Question.objects.in_scope(SCOPE_GLOBAL), kind=kind, slug=slug)
    if not question.is_formal:
        raise PermissionDenied(_("Only a formal vote is recorded."))
    if not _is_operator(request.user):
        raise PermissionDenied(_("Only staff close a vote."))

    services.record_decision(question, decided_by=request.user)
    messages.success(request, _("The vote is closed and its decision "
                                "is on the ledger."))
    return redirect(reverse("polls:question_results",
                            args=[question.kind, question.slug]))


def _filtered_ledger(request):
    """The ledger queryset plus the filters that shaped it — one place,
    because the HTML page and the PDF export must agree exactly."""
    decisions = Decision.objects.in_scope(SCOPE_GLOBAL)

    outcome = request.GET.get("outcome") or ""
    if outcome in Outcome.values:
        decisions = decisions.filter(outcome=outcome)

    filters = {"outcome": outcome}
    for param, lookup in (("from", "decided_at__date__gte"),
                          ("to", "decided_at__date__lte")):
        raw = request.GET.get(param) or ""
        filters[param] = raw
        if raw:
            try:
                decisions = decisions.filter(**{lookup: raw})
            except Exception:  # noqa: BLE001 — a malformed date is not an error page
                filters[param] = ""

    return decisions.select_related("question", "decided_by"), filters


@login_required
def decision_ledger(request):
    """What was decided, when, by which roll — the platform's public record."""
    services.record_overdue(SCOPE_GLOBAL)
    decisions, filters = _filtered_ledger(request)

    paginator = Paginator(decisions, LEDGER_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))

    querystring = "&".join(f"{k}={v}" for k, v in filters.items() if v)
    return _render(request, "polls/ledger.html", {
        "active_tab": "ledger",
        "page": page,
        "filters": filters,
        "outcomes": Outcome.choices,
        "querystring": querystring,
    })


def _pdf_response(request, build, filename: str):
    """The platform's metered-download shape (portfolio/views.py): quota and
    funds checked before the work, charged after it, refusals as plain text."""
    from toto.quota import QuotaExceeded, check_quota, record_usage
    from toto.quota.charge import (InsufficientFunds, charge, check_funds,
                                   price_for)

    tariff = price_for(request.user, "polls")
    try:
        check_quota(PollsQuotaPolicy, "polls.pdf", 1, request.user)
        check_funds(request.user, tariff, "polls.pdf", 1)
    except (QuotaExceeded, InsufficientFunds) as exc:
        # Plain text, not messages+redirect: this is a download target.
        return HttpResponse(str(exc), status=exc.status_code,
                            content_type="text/plain")

    try:
        raw = build()
    except render_pdf.PdfUnavailable as exc:
        # A deployment fact, not something the user can fix by retrying.
        return HttpResponse(str(exc), status=503, content_type="text/plain")

    record_usage(PollsUsageEvent, "polls.pdf", 1, request.user)
    charge(request.user, tariff, "polls.pdf", 1)

    response = HttpResponse(raw, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def decision_pdf(request, kind, slug):
    """One vote's recorded decision, as a document."""
    question = get_object_or_404(
        Question.objects.in_scope(SCOPE_GLOBAL), kind=kind, slug=slug)
    if not question.is_formal:
        raise PermissionDenied(_("Only a formal vote has a decision record."))

    roll = services.electorate_for(question)
    if not services.may_see_results(question, request.user, electorate=roll):
        raise PermissionDenied(_("The count is not open to you yet."))

    decision = get_object_or_404(Decision, question=question)
    base = slugify(question.title) or question.slug
    return _pdf_response(
        request, lambda: render_pdf.vote_result_pdf(question, decision),
        f"{base}-decision.pdf")


@login_required
def ledger_pdf_export(request):
    """The filtered ledger, on paper. Exactly the rows the HTML page shows."""
    services.record_overdue(SCOPE_GLOBAL)
    decisions, filters = _filtered_ledger(request)
    return _pdf_response(
        request,
        lambda: render_pdf.ledger_pdf(list(decisions), filters=filters),
        "decision-ledger.pdf")
