from django.shortcuts import render
from django.core.paginator import Paginator
from django.contrib.auth.decorators import login_required
from finance.models import Account, Currency, Asset
from oya.page import PageProcessor


def paginate(request, queryset, param, per_page=20):
    page_number = request.GET.get(param, 1)
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(page_number)


@login_required
def dashboard(request):
    # --- ACCOUNTS TAB ---
    accounts_qs = (
        Account.objects
        .select_related("owner", "currency", "manager")
        .all()
    )

    accounts_page = paginate(request, accounts_qs, "acc_page", per_page=8)

    # --- ASSETS TAB ---
    assets_qs = (
        Asset.objects
        .filter(is_active=True)
        .select_related("asset_type", "assigned_to")
        .order_by("-created_at")
    )

    assets_page = paginate(request, assets_qs, "asset_page", per_page=8)

    context = {
        "accounts_page": accounts_page,
        "assets_page": assets_page,
        "currencies": Currency.objects.filter(active=True)
    }

    return render(
        request,
        "finance/dashboard.html",
        PageProcessor().decorate(context, request)
    )
