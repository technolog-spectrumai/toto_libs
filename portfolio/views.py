from django.core.paginator import Paginator
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required

from .models import Company, FractionalOwnership
from oya.page import PageProcessor


@login_required
def company_list(request):
    """
    List all companies with pagination.
    """
    companies_qs = Company.objects.all().order_by("name")

    paginator = Paginator(companies_qs, 10)  # 10 companies per page
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
    }

    return render(
        request,
        "portfolio/company_list.html",
        PageProcessor().decorate(context, request)
    )

def resolve_entity_label(entity):
    """
    Returns a human-readable label for any SocialEntity subclass.
    """
    real_instnce = entity.get_real_instance()
    model = entity.get_real_instance_class().__name__
    if model == "CommunityMember":
        return f"Person {real_instnce.display_name}"
    elif model == "Company":
        return f"Company {real_instnce.name}"
    elif model == "Community":
        return f"Community {real_instnce.name}"
    return f"{model} {real_instnce.id}"

@login_required
def company_detail(request, pk):
    """
    Detail view for a single company, including ownership information.
    """
    company = get_object_or_404(
        Company.objects.select_related("headquarters"),
        pk=pk
    )

    # Ownership received (who owns this company)
    ownership_received = FractionalOwnership.objects.select_related(
        "owner_entity"
    ).filter(owned_company=company)

    ownership_received = [
        {
            "label": resolve_entity_label(o.owner_entity),
            "percentage": o.percentage,
        }
        for o in ownership_received
    ]
    chart_labels = [o["label"] for o in ownership_received]
    chart_values = [float(o["percentage"]) for o in ownership_received]

    context = {
        "company": company,
        "ownership_received": ownership_received,
        "chart_labels": chart_labels,
        "chart_values": chart_values,
        "company_data": {
            "id": company.id,
            "name": company.name,
            "registration_number": company.registration_number,
            "founded_date": company.founded_date,
            "website": company.website,
            "email": company.email,
            "headquarters": str(company.headquarters) if company.headquarters else None,
            "metadata": company.metadata or {},
            "created_at": company.created_at,
        },
    }

    return render(
        request,
        "portfolio/company_detail.html",
        PageProcessor().decorate(context, request)
    )
