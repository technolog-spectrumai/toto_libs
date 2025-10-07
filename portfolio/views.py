from django.shortcuts import render
from django.db.models import Sum, Count
from itertools import chain
from operator import attrgetter
from django.core.exceptions import ImproperlyConfigured
from django.utils.timezone import now
from .models import (
    Chamber, Venture, Transaction, Event, Associate
)
from .page import PageProcessor


def chamber_overview(request):
    # 🏛️ Load Active Chamber
    active_chambers = Chamber.objects.filter(active=True)

    if active_chambers.count() == 0:
        raise ImproperlyConfigured("No active chamber found. Please activate one in the admin panel.")
    elif active_chambers.count() > 1:
        raise ImproperlyConfigured("Multiple active chambers detected. Only one chamber can be active at a time.")

    chamber = active_chambers.first()

    # 🧠 Chamber Identity
    total_investors = chamber.investors.count()
    total_ventures = Venture.objects.count()

    # 💸 Total Investment in Default Currency
    if chamber.default_currency:
        total_investment = Transaction.objects.filter(
            currency=chamber.default_currency
        ).aggregate(total=Sum('amount'))['total'] or 0
    else:
        total_investment = None

    chamber_data = {
        "name": chamber.name,
        "manifest": chamber.manifest,
        "strategy": chamber.strategy,
        "created_at": chamber.created_at,
        "total_investors": total_investors,
        "total_ventures": int(total_ventures),
        "total_investment": round(total_investment, 2) if total_investment is not None else None
    }

    # 📊 Performance Summary
    performance_metrics = [
        {
            "label": "Total Investment",
            "currency": chamber.default_currency,
            "value": total_investment
        },
        {
            "label": "Total Venture Count",
            "value": int(total_ventures)
        },
    ]

    # 📦 Ventures with funding stats
    ventures = Venture.objects.annotate(
        funding_rounds_count=Count('funding_rounds'),
        total_funding=Sum('funding_rounds__amount')
    )

    # 📅 Recent Public Events (past only)
    recent_events = Event.objects.filter(
        owner__chamber=chamber,
        public=True,
        start__lte=now()
    ).order_by('-start')[:5]

    # 🚧 Coming Soon Public Events (future only)
    coming_soon = Event.objects.filter(
        owner__chamber=chamber,
        public=True,
        start__gt=now()
    ).order_by('start')[:5]

    # 🧑‍🤝‍🧑 Team = Associates + Chamber Investors
    associates = Associate.objects.filter(active=True)
    investors = chamber.investors.all()
    team = sorted(
        chain(associates, investors),
        key=attrgetter('joined_at'),
        reverse=True
    )

    context = {
        "chamber": chamber_data,
        "performance_metrics": performance_metrics,
        "ventures": ventures,
        "recent_events": recent_events,
        "coming_soon": coming_soon,
        "team": team
    }

    return render(request, "portfolio/main.html", PageProcessor().decorate(context, request))
