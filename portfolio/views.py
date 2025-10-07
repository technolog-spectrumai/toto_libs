from django.shortcuts import render
from django.db.models import Sum, Count
from itertools import chain
from operator import attrgetter
from .models import (
    Investor, Portfolio, Asset, Currency, Transaction,
    Event, Associate
)
from .page import PageProcessor

def fund_overview(request):
    # 🧠 Fund Identity
    total_investors = Investor.objects.count()
    total_portfolios = Portfolio.objects.count()
    total_value = Asset.objects.aggregate(total=Sum('quantity'))['total'] or 0

    fund_manifest = {
        "strategy": "Multi-chain crypto growth",
    }

    fund = {
        "name": "Oya Crypto Fund",
        "manifest": fund_manifest,
        "total_investors": total_investors,
        "total_portfolios": total_portfolios,
        "total_value": round(total_value, 2),
    }

    # 📊 Performance Summary
    performance_metrics = [
        {
            "label": "Total Investment",
            "value": Transaction.objects.aggregate(total=Sum('amount'))['total'] or 0
        },
        {
            "label": "Total Asset Count",
            "value": Asset.objects.count()
        },
    ]

    # 💼 Portfolios
    portfolios = Portfolio.objects.annotate(
        asset_count=Count('assets'),
        total_value=Sum('assets__quantity')
    )

    # 📅 Recent Activity
    recent_events = Event.objects.order_by('-timestamp')[:5]

    # 🚧 Coming Soon (from events)
    coming_soon = Event.objects.filter(
        event_type__in=["Feature", "Roadmap", "ComingSoon"],
        metadata__status__in=["planned", "upcoming"]
    ).order_by('timestamp')[:5]

    # 🧑‍🤝‍🧑 Team = Associates + Investors
    associates = Associate.objects.filter(active=True)
    investors = Investor.objects.all()
    team = sorted(
        chain(associates, investors),
        key=attrgetter('joined_at'),
        reverse=True
    )

    # 💸 Investment Breakdown
    investment_list = (
        Investor.objects
        .annotate(total_investment=Sum('transactions__amount'))
        .order_by('-total_investment')
    )

    context = {
        "fund": fund,
        "performance_metrics": performance_metrics,
        "portfolios": portfolios,
        "recent_events": recent_events,
        "coming_soon": coming_soon,
        "team": team,
        "investment_list": investment_list,
    }

    return render(request, "portfolio/main.html", PageProcessor().decorate(context, request))
