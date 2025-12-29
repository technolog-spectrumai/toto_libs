from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse
from finance.models import Account, Asset, Currency, Company
from oya.page import PageProcessor


@login_required
def dashboard(request):
    # Querysets
    accounts = (
        Account.objects
        .select_related("owner", "currency", "manager")
        .all()
    )

    assets = (
        Asset.objects
        .filter(is_active=True)
        .select_related("asset_type", "assigned_to")
        .order_by("-created_at")
    )

    companies = (
        Company.objects
        .select_related("headquarters")
        .order_by("name")
    )

    # ---------------------------------------------------------
    # ACCOUNTS TABLE
    # ---------------------------------------------------------
    accounts_columns = [
        {"label": "Name"},
        {"label": "Owner"},
        {"label": "Currency"},
        {"label": "Balance"},
        {"label": "Action"},
    ]

    accounts_rows = [
        [
            {"text": a.name, "type": "text"},
            {"text": str(a.owner), "type": "text"},
            {"text": str(a.currency), "type": "text"},
            {"text": a.balance, "type": "text"},
            {
                "text": "View",
                "url": reverse("finance:account-detail", args=[a.pk]),
                "type": "link",
            },
        ]
        for a in accounts
    ]

    # ---------------------------------------------------------
    # ASSETS TABLE
    # ---------------------------------------------------------
    assets_columns = [
        {"label": "Name"},
        {"label": "Type"},
        {"label": "Assigned To"},
        {"label": "Location"},
        {"label": "Action"},
    ]

    assets_rows = [
        [
            {"text": a.name, "type": "text"},
            {"text": a.asset_type.name, "type": "text"},
            {"text": a.assigned_to.display_name if a.assigned_to else "—", "type": "text"},
            {"text": a.location or "—", "type": "text"},
            {
                "text": "View",
                "url": reverse("finance:asset-detail", args=[a.pk]),
                "type": "link",
            },
        ]
        for a in assets
    ]

    # ---------------------------------------------------------
    # COMPANIES TABLE
    # ---------------------------------------------------------
    companies_columns = [
        {"label": "Name"},
        {"label": "Website"},
        {"label": "Headquarters"},
        {"label": "Action"},
    ]

    companies_rows = [
        [
            {"text": c.name, "type": "text"},
            {
                "text": c.website or "—",
                "url": c.website,
                "type": "link",
            },
            {
                "text": str(c.headquarters) if c.headquarters else "—",
                "type": "text",
            },
            {
                "text": "View",
                "url": reverse("finance:company-detail", args=[c.pk]),
                "type": "link",
            },
        ]
        for c in companies
    ]

    # ---------------------------------------------------------
    # CONTEXT
    # ---------------------------------------------------------
    context = {
        "accounts_columns": accounts_columns,
        "accounts_rows": accounts_rows,

        "assets_columns": assets_columns,
        "assets_rows": assets_rows,

        "companies_columns": companies_columns,
        "companies_rows": companies_rows,
    }

    return render(
        request,
        "finance/dashboard.html",
        PageProcessor().decorate(context, request)
    )
