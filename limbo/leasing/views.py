from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import LeaseForm
from .models import Lease, LeaseStatus
from .services import activate_lease, cancel_lease

try:
    from toto.ui import PageProcessor
except Exception:
    PageProcessor = None


def _render(request, template, context):
    if PageProcessor:
        context = PageProcessor().decorate(context, request)
    return render(request, template, context)


@login_required
def lease_list(request):
    status = request.GET.get("status") or None
    qs = Lease.objects.select_related(
        "leased_object", "lessor", "lessee", "payment_asset"
    ).order_by("-created_at")
    if status:
        qs = qs.filter(status=status)
    return _render(request, "leasing/lease_list.html", {
        "leases": qs[:100],
        "status": status,
        "status_choices": LeaseStatus.choices,
        "total": Lease.objects.count(),
        "active_count": Lease.objects.filter(status=LeaseStatus.ACTIVE).count(),
        "draft_count": Lease.objects.filter(status=LeaseStatus.DRAFT).count(),
    })


@login_required
def lease_detail(request, pk):
    lease = get_object_or_404(
        Lease.objects.select_related(
            "leased_object", "lessor", "lessee",
            "lessor_account", "lessee_account", "payment_asset", "contract",
        ),
        pk=pk,
    )
    return _render(request, "leasing/lease_detail.html", {"lease": lease})


@login_required
def lease_create(request):
    if request.method == "POST":
        form = LeaseForm(request.POST)
        if form.is_valid():
            lease = form.save()
            messages.success(request, "Lease created.")
            return redirect("leasing:lease_detail", pk=lease.pk)
    else:
        form = LeaseForm()
    return _render(request, "leasing/lease_form.html", {"form": form})


@require_POST
@login_required
def lease_activate(request, pk):
    lease = get_object_or_404(Lease, pk=pk)
    try:
        activate_lease(lease)
        messages.success(request, "Lease activated.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("leasing:lease_detail", pk=pk)


@require_POST
@login_required
def lease_cancel(request, pk):
    lease = get_object_or_404(Lease, pk=pk)
    try:
        cancel_lease(lease)
        messages.success(request, "Lease cancelled.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("leasing:lease_detail", pk=pk)
