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
from django.http import Http404
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from . import checkpoint as checkpoint_module
from . import downloads, render_pdf, services
from .core import Revisability, Visibility, VotingError
from .forms import VoteCreateForm
from .models import (SCOPE_GLOBAL, Choice, Decision, Kind, Outcome, Question)

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
def vote_record_paper(request):
    """Type in a paper vote's result — a secret ballot the room already held.

    Staff only, like every door that writes an immutable ledger row. The
    entry is results-only BY CONSTRUCTION: the form has no field that could
    attach a person to a choice, so there is nothing to hide later. The
    ledger labels it "Secret ballot" and its PDF carries the tally and the
    attestation, never individuals.
    """
    if not _is_operator(request.user):
        raise PermissionDenied(_("Only staff record a paper result."))

    from .forms import PaperResultForm

    form = PaperResultForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        decision = services.record_paper_result(
            title=form.cleaned_data["title"],
            question_text=form.cleaned_data["question_text"],
            options=form.cleaned_data["options"],
            method=form.cleaned_data["method"],
            electorate_label=form.cleaned_data["electorate_label"],
            electorate_size=form.cleaned_data["electorate_size"] or 0,
            note=form.cleaned_data["note"],
            recorded_by=request.user,
        )
        messages.success(request, _(
            "The result is on the ledger, marked as a secret ballot."))
        return redirect("polls:decision_ledger")

    return _render(request, "polls/paper_result_form.html", {
        "active_tab": "ledger",
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


def chain_of(electorate):
    """Verify one electorate's chain — or the platform's own unscoped one.

    Not every decision belongs to a roll: a platform-wide vote, and a paper
    result recorded without naming an electorate, both live on the global
    chain. Falling back to it means "no electorate selected" shows the ledger
    that does exist rather than an empty page.
    """
    from . import snapshots

    if electorate is None:
        return Decision.verify_chain(SCOPE_GLOBAL)
    return snapshots.verify_chain(electorate)


def selected_electorate(request):
    """The electorate whose ledger is being read, or None.

    ``?electorate=<slug>`` chooses; anything the requester does not belong to
    is a 404, so the parameter can never be used to reach another roll's
    decisions. With no parameter the first of their own is used — a person on
    several bodies gets one of theirs, never a global mixture, because there
    is no such thing as "everyone's ledger".
    """
    mine = my_electorates(request.user)
    slug = (request.GET.get("electorate") or "").strip()
    if slug:
        return get_object_or_404(mine, slug=slug)
    return mine.first()


def _filtered_ledger(request, electorate=None):
    """The rows of one electorate's ledger, plus the filters that shaped it.

    The filtering lives in services.filtered_decisions so the PAGE and its
    PDF export cannot disagree. Passing the electorate is what enforces
    isolation: a decision belonging to a roll the requester is not on is not
    filtered out of the page, it is never in the queryset.
    """
    if electorate is None:
        # No roll chosen (or none to choose): the platform's own ledger, the
        # global scope — exactly what this page showed before electorates
        # became selectable.
        return services.filtered_decisions(request.GET)
    return services.filtered_decisions(request.GET, electorate=electorate)


@login_required
def decision_ledger(request):
    """What was decided, when, by which roll — the platform's public record."""
    services.record_overdue(SCOPE_GLOBAL)
    electorate = selected_electorate(request)
    decisions, filters = _filtered_ledger(request, electorate)

    paginator = Paginator(decisions, LEDGER_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))

    querystring = "&".join(f"{k}={v}" for k, v in filters.items() if v)
    if electorate is not None:
        querystring = (f"electorate={electorate.slug}&" + querystring
                       ).rstrip("&")
    return _render(request, "polls/ledger.html", {
        "active_tab": "ledger",
        "page": page,
        "filters": filters,
        "outcomes": Outcome.choices,
        "querystring": querystring,
        "is_operator": _is_operator(request.user),
        "electorate": electorate,
        "my_electorates": my_electorates(request.user),
    })


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
    # The who-voted-how appendix is for the people the vote belonged to:
    # electorate members and staff. Everyone else who may see results gets
    # the same document without the appendix — the tally is public where the
    # ballots are not. (For a secret ballot the renderer prints no appendix
    # for anyone; its snapshot never contained one.)
    include_ballots = _is_operator(request.user) or question.roll.filter(
        user=request.user).exists()
    base = slugify(question.title) or question.slug
    return downloads.metered_pdf(
        request, lambda: render_pdf.vote_result_pdf(
            question, decision, include_ballots=include_ballots),
        f"{base}-decision.pdf")


@login_required
def ledger_pdf_export(request):
    """The filtered ledger, on paper. Exactly the rows the HTML page shows.

    ``?qr=1`` embeds a verification checkpoint: a FRESH one is taken at
    export time (and stored, like any checkpoint), so the document carries
    the head of the ledger it prints — the PDF becomes its own offline
    witness. Falls back to unembellished export if the fold cannot run.
    """
    services.record_overdue(SCOPE_GLOBAL)
    electorate = selected_electorate(request)
    decisions, filters = _filtered_ledger(request, electorate)

    checkpoint_row = None
    if request.GET.get("qr") == "1":
        from . import checkpoint

        checkpoint_row = checkpoint.take(
            scope_type=SCOPE_GLOBAL, scope_id="", by=request.user,
            note=_("embedded in a ledger PDF export"))
    return downloads.metered_pdf(
        request,
        lambda: render_pdf.ledger_pdf(list(decisions), filters=filters,
                                      checkpoint_row=checkpoint_row),
        "decision-ledger.pdf")

# -- electorates (stage 5) and the ledger's chain (stage 6) -------------------

def _checkpoint_qr(payload: str) -> str:
    """The payload as an inline QR data-URI, or "" when nothing can draw one.

    The payload TEXT is always shown beside the image (the sso pairing rule:
    a QR is a transport, never the only copy), so a host whose cv2 cannot
    encode simply shows the text to copy by hand.
    """
    try:
        from toto.core import qr

        return qr.render_data_uri(payload)
    except Exception:  # noqa: BLE001 - a missing encoder must not 500 the list
        return ""


@login_required
def ledger_checkpoints(request):
    """Every checkpoint taken of the global ledger, each with its live verdict.

    The page states the custody rule out loud: the stored row is the
    convenience, the printed QR is the evidence — an attacker who can rewrite
    decisions can rewrite this table, and cannot rewrite paper.
    """
    from . import checkpoint
    from .checkpoint_models import LedgerCheckpoint

    rows = []
    for stored in (LedgerCheckpoint.objects
                   .filter(scope_type=SCOPE_GLOBAL, scope_id="")[:50]):
        # A checkpoint row cannot be deleted (checkpoint_models.delete raises),
        # so one unparseable row must never take the page down with it — the
        # page's job is to show the others. Older builds could write such a row
        # by checkpointing an empty ledger; take() now refuses that, and this
        # keeps any already-stored one readable rather than fatal.
        try:
            result = checkpoint.verify_stored(stored)
        except checkpoint.PayloadError as exc:
            result = None
            unreadable = str(exc)
        else:
            unreadable = ""
        rows.append({"checkpoint": stored, "result": result,
                     "unreadable": unreadable,
                     "qr": _checkpoint_qr(stored.payload) if not unreadable else ""})

    count, head_hex = checkpoint.head_at(SCOPE_GLOBAL, "")
    return _render(request, "polls/ledger_checkpoints.html", {
        "active_tab": "ledger",
        "rows": rows,
        "current_count": count,
        "current_head": head_hex,
        "is_operator": _is_operator(request.user),
        "verify_result": None,
        "verify_payload": "",
    })


@login_required
@require_POST
def ledger_checkpoint_new(request):
    """Take a checkpoint now. Staff — it writes an immutable evidence row."""
    if not _is_operator(request.user):
        raise PermissionDenied(_("Only staff take a ledger checkpoint."))
    from . import checkpoint

    services.record_overdue(SCOPE_GLOBAL)
    stored = checkpoint.take(scope_type=SCOPE_GLOBAL, scope_id="",
                             by=request.user,
                             note=request.POST.get("note", "").strip()[:200])
    messages.success(request, _(
        "Checkpoint taken at %(count)s entries. Print or photograph the QR "
        "now and keep it OUTSIDE this platform — the stored copy is a "
        "convenience, the offline copy is the evidence.")
        % {"count": stored.entry_count})
    return redirect("polls:ledger_checkpoints")


@login_required
def ledger_checkpoint_verify(request):
    """Scan or paste a checkpoint and compare it against the stored ledger.

    Accepts the payload text or an uploaded QR photo. The verdict page says
    MATCH / MISMATCH and, on mismatch, where the damage is localized — or
    that the rewrite is self-consistent and which stored checkpoints bracket
    it. Any logged-in member may verify: an audit tool that only staff can
    run is not independent evidence.
    """
    from . import checkpoint

    result = None
    payload_text = ""
    error = ""
    if request.method == "POST":
        payload_text = (request.POST.get("payload") or "").strip()
        upload = request.FILES.get("image")
        if not payload_text and upload:
            try:
                from toto.core import qr

                payload_text = qr.read(upload.read())
            except Exception as exc:  # noqa: BLE001 - a bad photo is user input
                error = str(exc)
        if payload_text and not error:
            try:
                parsed = checkpoint.parse_payload(payload_text)
                result = checkpoint.verify(parsed)
            except checkpoint.PayloadError as exc:
                error = str(exc)
        elif not payload_text and not error:
            error = _("Paste the checkpoint text or upload a photo of the QR.")

    bracket_hit = bracket_miss = None
    if result is not None and result.verdict == "MISMATCH":
        bracket_hit, bracket_miss = checkpoint.bracket(SCOPE_GLOBAL, "")

    from .checkpoint_models import LedgerCheckpoint

    rows = []
    for stored in (LedgerCheckpoint.objects
                   .filter(scope_type=SCOPE_GLOBAL, scope_id="")[:50]):
        rows.append({"checkpoint": stored,
                     "result": checkpoint.verify_stored(stored),
                     "qr": _checkpoint_qr(stored.payload)})
    count, head_hex = checkpoint.head_at(SCOPE_GLOBAL, "")
    return _render(request, "polls/ledger_checkpoints.html", {
        "active_tab": "ledger",
        "rows": rows,
        "current_count": count,
        "current_head": head_hex,
        "is_operator": _is_operator(request.user),
        "verify_result": result,
        "verify_payload": payload_text,
        "verify_error": error,
        "bracket_hit": bracket_hit,
        "bracket_miss": bracket_miss,
    })


def _person_of(user):
    """This login's Person, or None. Never raises on a host without people."""
    try:
        return getattr(user, "community_profile", None)
    except Exception:  # noqa: BLE001 - a missing profile is not an error page
        return None


def my_electorates(user):
    """Every electorate this user may look at, across every scope.

    Members see the bodies they sit on — in any company, any community, as
    many as they belong to; nothing here assumes one default company. Staff
    see all of them, because they configure them.

    This is the fix for the bug where membership existed in the admin and
    showed nowhere: the pages asked for ``in_scope(SCOPE_GLOBAL)``, i.e.
    ``scope_type=""``, while every real electorate belongs to a company or a
    community, so the query could only ever return nothing.
    """
    from .electorate_models import Electorate

    if _is_operator(user):
        return Electorate.objects.all().order_by("name")
    return Electorate.objects.for_person(_person_of(user)).order_by("name")


def _electorate_or_404(request, slug):
    """One electorate the requester is entitled to see. 404 otherwise —
    a 403 would confirm that a body by this name exists elsewhere."""
    return get_object_or_404(my_electorates(request.user), slug=slug)


@login_required
def snapshot_list(request):
    """Snapshots of one electorate's ledger — states, not events.

    The page shows the ledger's current state, every stored snapshot of it,
    and the chain drawn as a chain. Any member of the roll may take one:
    taking a snapshot of an unchanged ledger is not a write, it resolves to
    the state that already exists.
    """
    from . import snapshots

    electorate = selected_electorate(request)
    count, head, last = (snapshots.head_of(electorate)
                         if electorate else (0, "", None))
    stored = list(snapshots.LedgerSnapshot.objects.filter(
        electorate=electorate)) if electorate else []
    rows = [{"snapshot": s,
             "qr": _checkpoint_qr(s.payload),
             "current": s.head_hash == head}
            for s in stored]

    # The chain, as data for the graph: nodes are entries, edges are the
    # prev_hash links. Drawn by sigma.js; the table below says the same thing
    # in words, because a graph nobody can copy out of is not a record.
    entries = []
    for index, decision in enumerate(snapshots.ledger_of(electorate), start=1):
        entries.append({
            "n": index,
            "pk": decision.pk,
            "title": decision.title,
            "outcome": decision.get_outcome_display(),
            "decided_at": decision.decided_at.strftime("%Y-%m-%d %H:%M"),
            "hash": decision.content_hash,
            "prev": decision.prev_hash,
            "secret": bool(decision.secret_ballot),
        })

    return _render(request, "polls/snapshots.html", {
        "active_tab": "snapshots",
        "electorate": electorate,
        "my_electorates": my_electorates(request.user),
        "current_count": count,
        "current_head": head,
        "ledger_modified_at": last,
        "rows": rows,
        "entries": entries,
        "entries_json": json.dumps(entries),
        "chain": chain_of(electorate),
        "verify_result": None,
    })


@login_required
@require_POST
def snapshot_take(request):
    """Resolve the current ledger state to its snapshot. Idempotent."""
    from . import snapshots

    electorate = selected_electorate(request)
    if electorate is None:
        raise Http404("No electorate.")
    snapshot, created = snapshots.snapshot_current(
        electorate, by=request.user,
        note=request.POST.get("note", "").strip()[:200])
    if snapshot is None:
        messages.info(request, _(
            "This ledger has no decisions yet, so it has no state to snapshot."))
    elif created:
        messages.success(request, _(
            "Snapshot taken at %(count)s entries. Keep the QR outside this "
            "platform — that copy is what proves the ledger later.")
            % {"count": snapshot.entry_count})
    else:
        messages.info(request, _(
            "This ledger state already had a snapshot — the same one is shown. "
            "A snapshot is a state, not an event."))
    return redirect(f"{reverse('polls:snapshots')}?electorate={electorate.slug}")


@login_required
@require_POST
def snapshot_delete(request, pk):
    """Forget a stored snapshot. The ledger is untouched.

    Deleting a snapshot deletes a convenience: the QR image and the row that
    remembered it. It cannot alter a Decision, and any QR already printed
    still verifies, because verification recomputes from the decisions
    themselves and never consults this table.
    """
    from . import snapshots

    snapshot = get_object_or_404(
        snapshots.LedgerSnapshot,
        pk=pk, electorate__in=my_electorates(request.user))
    slug = snapshot.electorate.slug
    snapshot.delete()
    messages.success(request, _(
        "Snapshot deleted. The Decision Ledger is unchanged, and any QR "
        "already taken still verifies."))
    return redirect(f"{reverse('polls:snapshots')}?electorate={slug}")


@login_required
def snapshot_verify(request):
    """Check any QR against the ledger it names — no stored row required."""
    from . import snapshots

    result = error = None
    payload_text = (request.POST.get("payload") or "").strip()
    if request.method == "POST":
        upload = request.FILES.get("image")
        if not payload_text and upload:
            try:
                from toto.core import qr

                payload_text = qr.read(upload.read())
            except Exception as exc:  # noqa: BLE001 - a bad photo is user input
                error = str(exc)
        if payload_text and not error:
            try:
                result = snapshots.verify_payload(payload_text)
            except checkpoint_module.PayloadError as exc:
                error = str(exc)
        elif not error:
            error = _("Paste the checkpoint text or upload a photo of the QR.")

    electorate = selected_electorate(request)
    count, head, last = (snapshots.head_of(electorate)
                         if electorate else (0, "", None))
    return _render(request, "polls/snapshot_verify.html", {
        "active_tab": "snapshots",
        "electorate": electorate,
        "my_electorates": my_electorates(request.user),
        "current_count": count,
        "current_head": head,
        "ledger_modified_at": last,
        "verify_result": result,
        "verify_error": error,
        "verify_payload": payload_text,
    })


@login_required
def electorate_list(request):
    """The rolls this person sits on. Staff configure; members read."""
    rolls = []
    for electorate in my_electorates(request.user).prefetch_related("members"):
        rolls.append({
            "electorate": electorate,
            "members": electorate.members.count(),
            "total_weight": electorate.total_weight(),
        })
    return _render(request, "polls/electorate_list.html", {
        "active_tab": "electorates",
        "rolls": rolls,
        "is_operator": _is_operator(request.user),
        "has_person": _person_of(request.user) is not None,
    })


@login_required
def electorate_detail(request, slug):
    """One roll: members, weights, percentages — and the power chart.

    Deliberately generic. Members and weights, never shares: the same table
    serves a company assembly, a club committee and a project board.
    """
    electorate = _electorate_or_404(request, slug)
    table = electorate.power_table()

    return _render(request, "polls/electorate_detail.html", {
        "active_tab": "electorates",
        "electorate": electorate,
        "table": table,
        "total_weight": electorate.total_weight(),
        "is_operator": _is_operator(request.user),
        "power_chart_json": json.dumps({
            "chart_type": "pie",
            "labels": [row["name"] for row in table],
            "datasets": [{"data": [row["weight"] for row in table],
                          "backgroundColor": PALETTE}],
        }) if table else "",
    })


@login_required
def ledger_verify(request):
    """Recompute the chain and name the first broken entry, if any.

    Detectable, not prevented: a decision cannot be edited or deleted through
    the model, and a raw-SQL rewrite leaves every later hash failing to
    verify. That is what this page reports, and the page says exactly that.
    """
    services.record_overdue(SCOPE_GLOBAL)
    electorate = selected_electorate(request)
    verification = chain_of(electorate)
    decisions, filters = _filtered_ledger(request, electorate)

    paginator = Paginator(decisions, LEDGER_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    querystring = "&".join(f"{k}={v}" for k, v in filters.items() if v)

    return _render(request, "polls/ledger.html", {
        "active_tab": "ledger",
        "page": page,
        "filters": filters,
        "outcomes": Outcome.choices,
        "querystring": querystring,
        "verification": verification,
        "is_operator": _is_operator(request.user),
        "electorate": electorate,
        "my_electorates": my_electorates(request.user),
    })
