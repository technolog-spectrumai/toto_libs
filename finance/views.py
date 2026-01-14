from oya.page import PageProcessor
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils.timezone import now
from django.db.models import Sum
from oya.page import PageProcessor
from .models import Account
import json
from datetime import timedelta


@login_required
def account_detail(request, pk):
    account = get_object_or_404(
        Account.objects.select_related("owner", "currency", "manager"),
        pk=pk,
        active=True,
    )

    # ---------------------------------------------------------
    # PERIOD SELECTION
    # ---------------------------------------------------------
    available_periods = [7, 14, 30, 60, 90]
    selected_period = int(request.GET.get("period", 30))
    if selected_period not in available_periods:
        selected_period = 30

    start_date = now() - timedelta(days=selected_period)

    # ---------------------------------------------------------
    # TRANSACTIONS
    # ---------------------------------------------------------
    outgoing = (
        account.transaction_outgoing
        .filter(timestamp__gte=start_date)
        .select_related("destination", "source__currency")
        .order_by("-timestamp")
    )

    incoming = (
        account.transaction_incoming
        .filter(timestamp__gte=start_date)
        .select_related("source", "source__currency")
        .order_by("-timestamp")
    )

    # ---------------------------------------------------------
    # BALANCE HISTORY (LINE CHART)
    # ---------------------------------------------------------
    # Build daily balance snapshots
    balance_dates = []
    balance_values = []

    # Start from current balance and walk backwards
    current_balance = account.balance

    # Build a list of days: 0 = today, 1 = yesterday, etc.
    for day in range(selected_period):
        day_date = now() - timedelta(days=day)
        day_start = day_date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        # Sum incoming/outgoing for that day
        incoming_sum = (
            account.transaction_incoming
            .filter(timestamp__gte=day_start, timestamp__lt=day_end)
            .aggregate(total=Sum("amount"))
            .get("total") or 0
        )

        outgoing_sum = (
            account.transaction_outgoing
            .filter(timestamp__gte=day_start, timestamp__lt=day_end)
            .aggregate(total=Sum("amount"))
            .get("total") or 0
        )

        # Reverse-apply the day's net change
        net_change = incoming_sum - outgoing_sum
        balance_values.append(float(current_balance))
        balance_dates.append(day)

        current_balance -= net_change

    balance_dates.reverse()
    balance_values.reverse()

    context = {
        "account": account,
        "transactions_incoming": incoming,
        "transactions_outgoing": outgoing,
        "available_periods": available_periods,
        "selected_period": selected_period,
        "balance_dates": json.dumps(balance_dates),
        "balance_values": json.dumps(balance_values),
    }

    return render(
        request,
        "finance/account_details.html",
        PageProcessor().decorate(context, request)
    )


@login_required
def account_list(request):
    """
    Account dashboard showing all active accounts in a table.
    """
    accounts = (
        Account.objects.filter(active=True)
        .select_related("owner", "currency", "manager")
        .order_by("name")
    )

    # Table column headers
    accounts_columns = [
        {"label": "Name"},
        {"label": "Owner"},
        {"label": "Currency"},
        {"label": "Balance"},
        {"label": "Created"},
    ]

    # Convert queryset into the row structure your template expects
    accounts_rows = []
    for acc in accounts:
        accounts_rows.append([
            {
                "type": "link",
                "text": acc.name,
                "url": reverse("finance:account_detail", args=[acc.pk]),
            },
            {
                "type": "text",
                "text": acc.owner.legal_name,
            },
            {
                "type": "text",
                "text": acc.currency.symbol,
            },
            {
                "type": "text",
                "text": f"{acc.balance}",
            },
            {
                "type": "text",
                "text": acc.created_at.strftime("%Y-%m-%d"),
            },
        ])

    context = {
        "accounts_columns": accounts_columns,
        "accounts_rows": accounts_rows,
    }

    return render(
        request,
        "finance/account_list.html",
        PageProcessor().decorate(context, request)
    )
