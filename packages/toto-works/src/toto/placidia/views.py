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

from collections import defaultdict

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import ListView

from toto.kanban.models import (
    Mission, RewardGrantState, RewardPolicy, RewardTrigger, Submission,
    SubmissionResolution, SubmissionState, visible_missions_for,
)
from toto.ui import PageProcessor

from .models import AcceptedObservation, PlacidiaBounty


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
