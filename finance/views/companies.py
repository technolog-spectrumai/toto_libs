from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from finance.models import Company, FractionalOwnership
from oya.page import PageProcessor


@login_required
def company_detail(request, pk):
    """
    Detail view for a single company, including ownership breakdown.
    """
    company = get_object_or_404(
        Company.objects.select_related("headquarters"),
        pk=pk
    )

    # Ownership (cap table)
    ownership_qs = (
        FractionalOwnership.objects
        .select_related("owner_entity")
        .filter(company=company)
    )

    ownership_received = []
    for o in ownership_qs:
        owner = o.owner_entity.get_real_instance() if o.owner_entity else None

        # Resolve label (Company, Community, Member, etc.)
        if owner is None:
            label = "Unknown"
        else:
            model = owner.__class__.__name__
            if hasattr(owner, "display_name"):
                label = owner.display_name
            elif hasattr(owner, "name"):
                label = owner.name
            else:
                label = f"{model} {owner.pk}"

        ownership_received.append({
            "label": label,
            "percentage": float(o.percentage),   # IMPORTANT: convert Decimal → float
        })

    # Chart data
    chart_labels = [o["label"] for o in ownership_received]
    chart_values = [o["percentage"] for o in ownership_received]

    context = {
        "company": company,
        "ownership_received": ownership_received,
        "chart_labels": chart_labels,
        "chart_values": chart_values,
    }

    return render(
        request,
        "finance/company_detail.html",
        PageProcessor().decorate(context, request)
    )
