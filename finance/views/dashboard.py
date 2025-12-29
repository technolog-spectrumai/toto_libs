from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from finance.models import Account, Asset, Currency, Company, FractionalOwnership
from oya.page import PageProcessor


@login_required
def dashboard(request):
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

    context = {
        "accounts": accounts,
        "assets": assets,
        "companies": companies,
        "currencies": Currency.objects.filter(active=True),
    }

    return render(
        request,
        "finance/dashboard.html",
        PageProcessor().decorate(context, request)
    )
