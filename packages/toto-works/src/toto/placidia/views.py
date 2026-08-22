"""The Bounty Board.

A SEPARATE surface from the kanban board, on purpose. The kanban board answers
"what is the state of our work" — status, assignees, blockers, sprints. This
answers "what can I contribute to, and what happened to what I sent" — open
bounties, reward, submissions, review progress, accepted observations, dataset
progress. Same engine underneath; different question, so a different page.

**Access is LoginRequired and deliberately NOT `in_data_mesh`.** Every other
kanban surface carries that gate (see ``kanban.views.MissionDetailView``), and
copying it here would be the obvious consistency — and wrong. Crowdsourcing
depends on people outside the core team being able to see what needs
collecting; a mesh gate would show the board to exactly the people who are not
its audience. Visibility is still enforced, through the same
``visible_missions_for`` the boards use, so a private mission never appears.
Do not "fix" this into consistency without reading this paragraph.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _g
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.views.generic import DetailView, ListView

from toto.kanban import work
from toto.kanban.models import (
    Mission, RewardGrantState, RewardPolicy, RewardTrigger, Submission,
    SubmissionResolution, SubmissionState, visible_missions_for,
)
from toto.people.models import Person
from toto.ui import PageProcessor

from . import services
from .forms import ContributionForm, ReviewForm
from .models import AcceptedObservation, Dataset, DatasetVersion, PlacidiaBounty


class BountyBoardView(LoginRequiredMixin, ListView):
    template_name = "placidia/board.html"
    context_object_name = "bounties"
    paginate_by = 24

    #: ?show=open (default) | closed | all
    def _show(self):
        value = (self.request.GET.get("show") or "open").lower()
        return value if value in {"open", "closed", "all"} else "open"

    def get_queryset(self):
        visible = visible_missions_for(self.request.user, Mission.objects.all())
        qs = (PlacidiaBounty.objects
              .filter(mission__in=visible)
              .select_related(
                  "mission",
                  "mission__campaign",
                  "mission__campaign__placidia",
                  "mission__zone",
                  "mission__location",
              ))

        query = (self.request.GET.get("q") or "").strip()
        if query:
            qs = qs.filter(
                Q(mission__title__icontains=query)
                | Q(instructions__icontains=query)
                | Q(mission__campaign__name__icontains=query))

        # Submission-side counts annotate cleanly: they all walk the same
        # relation, so one join and filtered Counts. The observation count does
        # NOT come along for the ride — a second multi-valued join here would
        # multiply the rows and inflate every count on the page. It is attached
        # separately in get_context_data.
        qs = qs.annotate(
            n_submissions=Count(
                "mission__tasks__submissions",
                filter=~Q(mission__tasks__submissions__state=SubmissionState.DRAFT),
                distinct=True),
            n_awaiting=Count(
                "mission__tasks__submissions",
                filter=Q(
                    mission__tasks__submissions__state=SubmissionState.SUBMITTED,
                    mission__tasks__submissions__resolution=SubmissionResolution.PENDING),
                distinct=True),
        )
        return qs.order_by("-created_at", "-pk")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)

        bounties = list(context["bounties"])
        now = timezone.now()

        self._attach_accepted_counts(bounties)
        self._attach_rewards(bounties)
        for bounty in bounties:
            bounty.open_now = bounty.is_open(now=now)

        show = self._show()
        if show == "open":
            bounties = [b for b in bounties if b.open_now]
        elif show == "closed":
            bounties = [b for b in bounties if not b.open_now]

        context.update({
            "bounties": bounties,
            "show": show,
            "show_choices": (
                ("open", _("Open")),
                ("closed", _("Closed")),
                ("all", _("All")),
            ),
            "query": (self.request.GET.get("q") or "").strip(),
            "totals": {
                "open": sum(1 for b in bounties if b.open_now),
                "accepted": sum(b.n_accepted for b in bounties),
                "awaiting": sum(b.n_awaiting for b in bounties),
            },
            "my_gems": self._my_gems(),
        })
        return context

    @staticmethod
    def _attach_accepted_counts(bounties):
        """One grouped query for all of them, rather than one per card."""
        counts = dict(
            AcceptedObservation.objects
            .filter(bounty__in=bounties)
            .values_list("bounty_id")
            .annotate(n=Count("pk"))
        )
        for bounty in bounties:
            bounty.n_accepted = counts.get(bounty.pk, 0)

    @staticmethod
    def _attach_rewards(bounties):
        """The collection reward per bounty: its own, else its campaign's.

        Read from the kanban RewardPolicy rather than from
        ``PlacidiaBounty.reward_summary``, which is display text somebody may
        have forgotten to update. A number on a board is a promise.
        """
        mission_ids = [b.mission_id for b in bounties]
        campaign_ids = {b.mission.campaign_id for b in bounties}
        policies = RewardPolicy.objects.filter(
            active=True,
            trigger=RewardTrigger.SUBMISSION_ACCEPTED,
        ).filter(Q(mission_id__in=mission_ids)
                 | Q(campaign_id__in=campaign_ids, mission__isnull=True))

        by_mission, by_campaign = {}, {}
        for policy in policies:
            if policy.mission_id:
                by_mission[policy.mission_id] = policy
            elif policy.campaign_id:
                by_campaign[policy.campaign_id] = policy

        for bounty in bounties:
            bounty.reward = (by_mission.get(bounty.mission_id)
                             or by_campaign.get(bounty.mission.campaign_id))

    def _my_gems(self):
        """What this viewer has actually been credited, across all bounties.

        SETTLED only. A pending or failed grant is not money somebody has, and
        showing it as a balance would be the board making a promise the ledger
        has not kept.
        """
        from toto.people.models import Person

        person = Person.objects.filter(user=self.request.user).first()
        if person is None:
            return None
        total = (person.kanban_reward_grants
                 .filter(state=RewardGrantState.SETTLED)
                 .aggregate(total=Sum("amount_base_units"))["total"]) or 0
        return {"total": total}


# ── helpers ──────────────────────────────────────────────────────────────────

def _visible_bounties(user):
    """Bounties whose mission this user may see. One definition, every view."""
    return PlacidiaBounty.objects.filter(
        mission__in=visible_missions_for(user, Mission.objects.all()))


def _visible_datasets(user):
    """Datasets under campaigns whose bounties this user can see.

    A plain function, not ``DatasetListView.get_queryset``: three views and one
    POST endpoint need this set, and reaching into a view class for it means
    passing something that is not that view as ``self`` — which works right up
    until the method touches an attribute the impostor lacks.
    """
    campaign_ids = set(
        _visible_bounties(user)
        .values_list("mission__campaign_id", flat=True))
    return (Dataset.objects
            .filter(campaign__campaign_id__in=campaign_ids)
            .select_related("campaign__campaign")
            .prefetch_related("versions"))


def _person_or_404(user):
    person = Person.objects.filter(user=user).first()
    if person is None:
        # A signed-in account with no Person cannot contribute or review:
        # every row in the engine is keyed on Person, not on User.
        raise Http404("No person record for this account.")
    return person


# ── one bounty ───────────────────────────────────────────────────────────────

class BountyDetailView(LoginRequiredMixin, DetailView):
    """What a contributor needs before deciding to answer a call."""

    template_name = "placidia/bounty.html"
    context_object_name = "bounty"

    def get_queryset(self):
        # Invisible bounties 404 rather than 403, matching how kanban scopes
        # missions: a private call's existence must not leak.
        return _visible_bounties(self.request.user).select_related(
            "mission", "mission__campaign", "mission__campaign__placidia",
            "mission__zone", "mission__location")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)
        bounty = self.object
        person = Person.objects.filter(user=self.request.user).first()

        mine = []
        if person is not None:
            mine = list(
                Submission.objects
                .filter(task__mission_id=bounty.mission_id, submitted_by=person)
                .prefetch_related("reviews", "files__vault_file")
                .order_by("-created_at"))

        context.update({
            "open_now": bounty.is_open(),
            "policy": bounty.mission.effective_consensus_policy,
            "reward": _collection_reward(bounty),
            "my_submissions": mine,
            "counts": {
                "sent": bounty.submission_count(),
                "awaiting": bounty.awaiting_review_count(),
                "accepted": bounty.accepted_count(),
            },
        })
        return context


def _collection_reward(bounty):
    """The per-acceptance reward in force: the bounty's own, else its campaign's."""
    return (RewardPolicy.objects
            .filter(active=True, trigger=RewardTrigger.SUBMISSION_ACCEPTED)
            .filter(Q(mission_id=bounty.mission_id)
                    | Q(campaign_id=bounty.mission.campaign_id,
                        mission__isnull=True))
            .order_by("mission_id")
            .last())


# ── contributing ─────────────────────────────────────────────────────────────

@login_required
def contribute(request, pk):
    """Send an observation.

    The whole flow in one place: ``services.contribute`` mints this person's
    own Task under the bounty and opens a draft, files are attached while it is
    still a draft, and ``work.submit`` closes the one-way door LAST. Order
    matters — ``SubmissionFile`` rows cannot be added after the freeze, which
    is the point of the freeze.
    """
    bounty = get_object_or_404(_visible_bounties(request.user), pk=pk)
    person = _person_or_404(request.user)

    if not bounty.is_open():
        messages.error(request, _g("This bounty is not accepting contributions."))
        return redirect("placidia:bounty", pk=bounty.pk)

    if request.method != "POST":
        form = ContributionForm(user=request.user)
        return render(request, "placidia/contribute.html",
                      PageProcessor().decorate(
                          {"bounty": bounty, "form": form}, request))

    form = ContributionForm(request.POST, user=request.user)
    if not form.is_valid():
        return render(request, "placidia/contribute.html",
                      PageProcessor().decorate(
                          {"bounty": bounty, "form": form}, request))

    try:
        submission = services.contribute(
            bounty, person, notes=form.cleaned_data["notes"])
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("placidia:bounty", pk=bounty.pk)

    submission.notes = form.cleaned_data["notes"]
    submission.metadata = {
        "observed_at": (form.cleaned_data["observed_at"].isoformat()
                        if form.cleaned_data["observed_at"] else None),
    }
    submission.save(update_fields=["notes", "metadata"])

    from toto.kanban.models import SubmissionFile

    for vault_file in form.cleaned_data["files"]:
        SubmissionFile.objects.get_or_create(
            submission=submission, vault_file=vault_file)

    work.submit(submission)
    messages.success(
        request, _g("Sent. It is now waiting for review."))
    return redirect("placidia:bounty", pk=bounty.pk)


# ── reviewing ────────────────────────────────────────────────────────────────

class ReviewQueueView(LoginRequiredMixin, ListView):
    """Everything this person may review, and nothing they may not.

    Eligibility is ``work.can_review`` — the same helper the boards and the API
    use — so a reviewer sees the same set here as anywhere else, including the
    rule that nobody reviews their own submission.
    """

    template_name = "placidia/review_queue.html"
    context_object_name = "submissions"
    paginate_by = 25

    def get_queryset(self):
        bounties = _visible_bounties(self.request.user)
        candidates = (
            Submission.objects
            .filter(task__mission__placidia_bounty__in=bounties,
                    state=SubmissionState.SUBMITTED,
                    resolution=SubmissionResolution.PENDING)
            .select_related("task__mission__campaign", "submitted_by")
            .prefetch_related("reviews", "files__vault_file")
            .order_by("submitted_at"))
        # can_review is per-row (it consults the task roster and the
        # submitter), so the filter happens in Python. The candidate set is
        # already bounded to submitted-and-pending on visible bounties.
        return [s for s in candidates if work.can_review(self.request.user, s)]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)
        context["form"] = ReviewForm()
        return context


@login_required
@require_POST
def review(request, pk):
    """Record one verdict, then resolve if the policy is satisfied.

    ``work.resolve`` is idempotent and returns unchanged while consensus is
    still pending, so calling it after every review is correct and cheap — it
    is what makes the Nth reviewer the one who settles it, without anybody
    having to know which N is.
    """
    submission = get_object_or_404(
        Submission.objects.select_related("task__mission__campaign"), pk=pk)

    if not work.can_review(request.user, submission):
        messages.error(request, _g("You cannot review this submission."))
        return redirect("placidia:review_queue")

    form = ReviewForm(request.POST)
    if not form.is_valid():
        messages.error(request, _g("Pick a verdict."))
        return redirect("placidia:review_queue")

    person = _person_or_404(request.user)
    try:
        work.record_review(
            submission, person,
            form.cleaned_data["verdict"], form.cleaned_data["comment"])
        resolved = work.resolve(submission)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("placidia:review_queue")

    if resolved.resolution == SubmissionResolution.ACCEPTED:
        # Consensus said yes, so the claim becomes a fact. `accept` re-checks
        # that the resolution came from consensus and is idempotent, so this
        # is a convenience rather than a second door: the same call is what
        # `accept_all_pending` makes for anything resolved elsewhere.
        try:
            services.accept(resolved)
            messages.success(
                request, _g("Accepted — it is now part of the dataset."))
        except ValidationError as exc:
            messages.warning(request, "; ".join(exc.messages))
    elif resolved.resolution == SubmissionResolution.PENDING:
        messages.success(request, _g("Review recorded. Consensus is still pending."))
    else:
        messages.success(
            request, _g("Review recorded: %(outcome)s.")
            % {"outcome": resolved.get_resolution_display()})
    return redirect("placidia:review_queue")


# ── datasets ─────────────────────────────────────────────────────────────────

class DatasetListView(LoginRequiredMixin, ListView):
    template_name = "placidia/datasets.html"
    context_object_name = "datasets"

    def get_queryset(self):
        return _visible_datasets(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


class DatasetDetailView(LoginRequiredMixin, DetailView):
    template_name = "placidia/dataset.html"
    context_object_name = "dataset"

    def get_queryset(self):
        return _visible_datasets(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)
        context.update({
            "versions": list(self.object.versions.all()),
            "live_count": self.object.live_observation_count(),
            "can_freeze": self.request.user.is_staff or self.request.user.is_superuser,
        })
        return context


class DatasetVersionDetailView(LoginRequiredMixin, DetailView):
    """A frozen release: what it holds, and whether it still holds it."""

    template_name = "placidia/dataset_version.html"
    context_object_name = "version"

    def get_queryset(self):
        return DatasetVersion.objects.filter(
            dataset__in=_visible_datasets(self.request.user)
        ).select_related("dataset", "frozen_by")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)
        context.update({
            "members": list(
                self.object.members
                .select_related("observation__observed_by", "observation__bounty__mission")
                .order_by("observation__uid")),
            "verified": self.object.verify(),
        })
        return context


@login_required
@require_POST
def freeze_dataset(request, pk):
    """Publish a release. Staff only — a release is a public claim."""
    dataset = get_object_or_404(_visible_datasets(request.user), pk=pk)
    if not (request.user.is_staff or request.user.is_superuser):
        messages.error(request, _g("Only staff may publish a dataset version."))
        return redirect("placidia:dataset", pk=dataset.pk)

    version = services.freeze(
        dataset, by=Person.objects.filter(user=request.user).first(),
        notes=request.POST.get("notes", "").strip())
    messages.success(
        request,
        _g("Published version %(n)s with %(c)s observations.")
        % {"n": version.number, "c": version.members.count()})
    return redirect("placidia:dataset_version", pk=version.pk)
