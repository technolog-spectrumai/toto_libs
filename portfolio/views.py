from django.shortcuts import render, get_object_or_404
from django.views.generic import ListView, DetailView
from django.db.models import Count, Sum
from .models import Venture, Transaction, Currency, Chamber, Company, Shareholder
from .page import PageProcessor
from django.core.exceptions import ImproperlyConfigured


# 🏢 Company Detail View
class CompanyDetailView(DetailView):
    model = Company
    template_name = 'portfolio/company_detail.html'
    context_object_name = 'company'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        shareholders = Shareholder.objects.filter(company=self.object)
        context['shareholders'] = shareholders
        context['total_shares'] = sum(s.shares_owned for s in shareholders)
        context['share_labels'] = [s.full_name for s in shareholders]
        context['share_data'] = [s.shares_owned for s in shareholders]
        return PageProcessor().decorate(context, self.request)

# 💸 Fund Overview View
def fund_overview(request):

    active_chambers = Chamber.objects.filter(active=True)

    if active_chambers.count() == 0:
        raise ImproperlyConfigured("No active chamber found. Please activate one in the admin panel.")
    elif active_chambers.count() > 1:
        raise ImproperlyConfigured("Multiple active chambers detected. Only one chamber can be active at a time.")

    chamber = active_chambers.first()

    total_ventures = Venture.objects.count()
    total_transactions = Transaction.objects.count()

    currency_totals = Currency.objects.filter(active=True).annotate(
        total_investment=Sum('transaction__amount')
    )

    performance_metrics = [
        {
            "label": "Total Ventures",
            "value": total_ventures
        },
        {
            "label": "Total Transactions",
            "value": total_transactions
        },
    ]

    for currency in currency_totals:
        performance_metrics.append({
            "label": f"Total in {currency.symbol}",
            "currency": currency,
            "value": round(currency.total_investment or 0, 2)
        })

    ventures = Venture.objects.annotate(
        funding_rounds_count=Count('funding_rounds'),
        total_funding=Sum('funding_rounds__amount')
    )

    companies = Company.objects.annotate(
        venture_count=Count('ventures'),
        total_funding=Sum('ventures__funding_rounds__amount')
    ).filter(venture_count__gt=0)

    context = {
        "performance_metrics": performance_metrics,
        "ventures": ventures,
        "companies": companies,
        "chamber": chamber
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

    return render(request, "portfolio/fund_overview.html", decorated_context)
