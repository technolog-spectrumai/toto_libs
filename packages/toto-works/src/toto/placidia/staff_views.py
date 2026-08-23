"""Running a collection programme: the staff side.

Every view here is gated on ``is_staff`` — the one role the brief names for
controlling who runs Placidia. Contributors and reviewers never reach these.

Deleting a bounty is the one destructive thing, and it is bounded: a bounty
whose contributions have been ACCEPTED cannot be deleted, because its
``AcceptedObservation`` rows are provenance for a dataset and sit behind
``PROTECT``. Close it instead. The delete view says so rather than 500ing on
``ProtectedError``.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from toto.kanban import rewards
from toto.kanban.models import RewardGrant, RewardGrantState
from toto.people.models import Person
from toto.ui import PageProcessor

from .forms import BountyForm, CampaignForm
from .models import AcceptedObservation, PlacidiaBounty, PlacidiaCampaign


def _is_staff(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)


staff_required = user_passes_test(_is_staff)


def _person(request):
    return Person.objects.filter(user=request.user).first()


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


# ── the hub ──────────────────────────────────────────────────────────────────

@login_required
@staff_required
def manage(request):
    """Everything a programme runner needs on one page."""
    campaigns = (PlacidiaCampaign.objects
                 .select_related("campaign__project", "treasury_account", "gem_asset")
                 .order_by("campaign__name"))
    bounties = (PlacidiaBounty.objects
                .select_related("mission__campaign")
                .annotate(n_accepted=Count("observations", distinct=True))
                .order_by("-created_at"))

    grants = RewardGrant.objects.aggregate(
        pending=Count("pk", filter=Q(state=RewardGrantState.PENDING)),
        failed=Count("pk", filter=Q(state=RewardGrantState.FAILED)),
        settled=Count("pk", filter=Q(state=RewardGrantState.SETTLED)),
        skipped=Count("pk", filter=Q(state=RewardGrantState.SKIPPED)),
        owed=Sum("amount_base_units",
                 filter=Q(state__in=[RewardGrantState.PENDING,
                                     RewardGrantState.FAILED])),
    )
    recent_failures = (RewardGrant.objects
                       .filter(state=RewardGrantState.FAILED)
                       .select_related("recipient")
                       .order_by("-created_at")[:5])

    return _render(request, "placidia/manage.html", {
        "campaigns": campaigns,
        "bounties": bounties,
        "grants": grants,
        "recent_failures": recent_failures,
    })


# ── campaigns ────────────────────────────────────────────────────────────────

@login_required
@staff_required
def campaign_create(request):
    form = CampaignForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        campaign = form.save(owner=_person(request))
        messages.success(request, _("Campaign “%(n)s” created.") % {"n": campaign.name})
        return redirect("placidia:manage")
    return _render(request, "placidia/campaign_form.html", {"form": form})


# ── bounties ─────────────────────────────────────────────────────────────────

@login_required
@staff_required
def bounty_create(request):
    form = BountyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            bounty = form.save(owner=_person(request))
        messages.success(request, _("Bounty “%(t)s” created.") % {"t": bounty.mission.title})
        return redirect("placidia:bounty", pk=bounty.pk)
    return _render(request, "placidia/bounty_form.html",
                   {"form": form, "bounty": None})


@login_required
@staff_required
def bounty_edit(request, pk):
    bounty = get_object_or_404(
        PlacidiaBounty.objects.select_related("mission__campaign"), pk=pk)
    form = BountyForm(request.POST or None, instance=bounty)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save(owner=_person(request))
        messages.success(request, _("Bounty updated."))
        return redirect("placidia:bounty", pk=bounty.pk)
    return _render(request, "placidia/bounty_form.html",
                   {"form": form, "bounty": bounty})


@login_required
@staff_required
def bounty_delete(request, pk):
    bounty = get_object_or_404(
        PlacidiaBounty.objects.select_related("mission__campaign"), pk=pk)
    accepted = AcceptedObservation.objects.filter(bounty=bounty).count()

    if request.method == "POST":
        if accepted:
            messages.error(request, _(
                "This bounty has %(n)s accepted observation(s), which are "
                "provenance for the dataset. Close it instead of deleting it.")
                % {"n": accepted})
            return redirect("placidia:bounty_delete", pk=bounty.pk)
        title = bounty.mission.title
        try:
            with transaction.atomic():
                # The Mission cascades to Tasks, Submissions and Reviews —
                # everything contributed but never accepted goes with it,
                # which is what deleting a bounty means.
                bounty.mission.delete()
        except ProtectedError:
            messages.error(request, _("Something still references this bounty."))
            return redirect("placidia:bounty_delete", pk=bounty.pk)
        messages.success(request, _("Bounty “%(t)s” deleted.") % {"t": title})
        return redirect("placidia:manage")

    return _render(request, "placidia/bounty_confirm_delete.html", {
        "bounty": bounty,
        "accepted": accepted,
        "submissions": bounty.submission_count(),
    })


@login_required
@staff_required
@require_POST
def bounty_toggle(request, pk):
    """Open or close without deleting — the safe way to stop a bounty."""
    from django.utils import timezone

    bounty = get_object_or_404(PlacidiaBounty, pk=pk)
    if bounty.closes_at and bounty.closes_at <= timezone.now():
        bounty.closes_at = None
        messages.success(request, _("Bounty reopened."))
    else:
        bounty.closes_at = timezone.now()
        messages.success(request, _("Bounty closed."))
    bounty.save(update_fields=["closes_at"])
    return redirect("placidia:bounty", pk=bounty.pk)


# ── rewards ──────────────────────────────────────────────────────────────────

@login_required
@staff_required
@require_POST
def distribute_rewards(request):
    """Settle every grant that is pending or failed.

    Idempotent by construction: a grant carries a unique reference the ledger
    refuses to pay twice, and ``retry_failed`` resets only ``failed`` rows.
    Pressing this button five times pays once.
    """
    pending = list(RewardGrant.objects
                   .filter(state=RewardGrantState.PENDING)
                   .values_list("pk", flat=True))
    if pending:
        rewards.settle(pending)
    retried = rewards.retry_failed()

    after = RewardGrant.objects.aggregate(
        settled=Count("pk", filter=Q(state=RewardGrantState.SETTLED)),
        failed=Count("pk", filter=Q(state=RewardGrantState.FAILED)))
    messages.success(request, _(
        "Distribution run: %(p)s pending and %(r)s failed grant(s) attempted. "
        "Now %(s)s settled, %(f)s still failing.")
        % {"p": len(pending), "r": retried,
           "s": after["settled"], "f": after["failed"]})
    return redirect("placidia:manage")
