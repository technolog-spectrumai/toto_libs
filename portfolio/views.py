from django.shortcuts import render
from django.views.generic import DetailView
from .models import Venture, Transaction, Currency, Chamber, Company, Shareholder, FundingRound
from oya.page import PageProcessor
from django.db.models import Count, Sum, F
from django.core.exceptions import ImproperlyConfigured
from collections import defaultdict


# 🏢 Company Detail View
class CompanyDetailView(DetailView):
    model = Company
    template_name = 'portfolio/company_detail.html'
    context_object_name = 'company'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = self.object

        # 👥 Shareholders
        shareholders = Shareholder.objects.filter(company=company)
        context['shareholders'] = shareholders
        context['total_shares'] = sum(s.shares_owned for s in shareholders)
        context['share_labels'] = [s.full_name for s in shareholders]
        context['share_data'] = [s.shares_owned for s in shareholders]

        # 💸 All funding rounds for this company’s ventures
        funding_rounds = (
            FundingRound.objects
            .select_related("venture", "currency")
            .filter(venture__company=company)
            .order_by("-timestamp")
        )
        context["funding_rounds"] = funding_rounds

        # 🏛️ Chamber info
        chamber = Chamber.objects.filter(active=True).first()
        context["chamber"] = chamber

        return PageProcessor().decorate(context, self.request)


def fund_overview(request):
    # 🏛️ Ensure single active chamber
    active_chambers = Chamber.objects.filter(active=True)
    if active_chambers.count() == 0:
        raise ImproperlyConfigured("No active chamber found. Please activate one in the admin panel.")
    elif active_chambers.count() > 1:
        raise ImproperlyConfigured("Multiple active chambers detected. Only one chamber can be active at a time.")
    chamber = active_chambers.first()

    # 📦 Ventures with funding round count
    ventures = list(Venture.objects.annotate(
        funding_rounds_count=Count('funding_rounds')
    ))

    # 💸 Funding totals grouped by venture and currency
    raw_funding = (
        FundingRound.objects
        .values("venture__id", "currency__symbol")
        .annotate(total=Sum("amount"))
        .order_by("venture__id", "currency__symbol")
    )

    # 🧮 Attach funding to each venture
    funding_map = defaultdict(dict)
    for row in raw_funding:
        funding_map[row["venture__id"]][row["currency__symbol"]] = row["total"]

    for venture in ventures:
        venture.funding_by_currency = funding_map.get(venture.id, {})

    # 📦 Final context
    context = {
        "ventures": ventures,
        "chamber": chamber
    }

    # 🎨 Theme and chart colors
    decorated_context = PageProcessor().decorate(context, request)
    theme_colors = decorated_context.get("theme", {}).get("colors", {})
    decorated_context["chart_colors"] = {
        "background_light": theme_colors.get("accent-light", "#36A2EB"),
        "border_light": theme_colors.get("text-main-light", "#000000"),
        "text_light": theme_colors.get("text-main-light", "#000000"),
        "grid_light": "#444444",
        "background_dark": theme_colors.get("accent-dark", "#FFCE56"),
        "border_dark": theme_colors.get("text-main-dark", "#FFFFFF"),
        "text_dark": theme_colors.get("text-main-dark", "#FFFFFF"),
        "grid_dark": "#aaaaaa"
    }

    return render(request, "portfolio/fund_overview.html", decorated_context)


