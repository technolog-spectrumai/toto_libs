from django.shortcuts import render
from django.db.models import Sum, Count
from django.core.exceptions import ImproperlyConfigured
from operator import attrgetter
from .models import Chamber, Venture, Transaction
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
    total_investment = None
    if chamber.default_currency:
        total_investment = Transaction.objects.filter(
            currency=chamber.default_currency
        ).aggregate(total=Sum('amount'))['total'] or 0

    chamber_data = {
        "name": chamber.name,
        "manifest": chamber.manifest,
        "strategy": chamber.strategy,
        "created_at": chamber.created_at,
        "total_investors": total_investors,
        "total_ventures": total_ventures,
        "total_investment": round(total_investment, 2) if total_investment is not None else None,
        "total_stock_emitted": float(chamber.total_stock_emitted)
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
            "value": total_ventures
        },
    ]

    # 📦 Ventures with funding stats
    ventures = Venture.objects.annotate(
        funding_rounds_count=Count('funding_rounds'),
        total_funding=Sum('funding_rounds__amount')
    )

    team = []
    total_stock = float(chamber.total_stock_emitted or 0)

    for investor in chamber.investors.all():
        stock_owned = float(getattr(investor, "stock_owned", 0))
        ownership_percent = (stock_owned / total_stock * 100) if total_stock > 0 else 0.0

        team.append({
            "display_name": investor.display_name,

            "joined_at": investor.joined_at,
            "stock_owned": stock_owned,
            "ownership_percent": round(ownership_percent, 2)
        })

    # 🧩 Placeholder for optional context
    recent_events = []  # Replace with actual query if Event model is added
    coming_soon = []    # Replace with actual logic if needed

    context = {
        "chamber": chamber_data,
        "performance_metrics": performance_metrics,
        "ventures": ventures,
        "recent_events": recent_events,
        "coming_soon": coming_soon,
        "team": team
    }

    decorated_context = PageProcessor().decorate(context, request)
    theme_colors = decorated_context.get("theme", {}).get("colors", {})

    chart_colors = {
        "background_light": theme_colors.get("accent-light", "#36A2EB"),
        "border_light": theme_colors.get("text-main-light", "#000000"),
        "text_light": theme_colors.get("text-main-light", "#000000"),
        "grid_light": "#444444",
        "background_dark": theme_colors.get("accent-dark", "#FFCE56"),
        "border_dark": theme_colors.get("text-main-dark", "#FFFFFF"),
        "text_dark": theme_colors.get("text-main-dark", "#FFFFFF"),
        "grid_dark": "#aaaaaa"
    }

    decorated_context["chart_colors"] = chart_colors

    return render(request, "portfolio/main.html", decorated_context)
