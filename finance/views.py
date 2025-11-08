from datetime import timedelta

from django.utils.timezone import now
from django.views.generic import ListView, DetailView
from .models import Account, Currency
from community.page import PageProcessor


def get_chart_colors(theme):
    return {
        "background_light": theme.get("accent-light", "#36A2EB"),
        "border_light": theme.get("text-main-light", "#000000"),
        "text_light": theme.get("text-main-light", "#000000"),
        "grid_light": "#444444",
        "background_dark": theme.get("accent-dark", "#FFCE56"),
        "border_dark": theme.get("text-main-dark", "#FFFFFF"),
        "text_dark": theme.get("text-main-dark", "#FFFFFF"),
        "grid_dark": "#aaaaaa"
    }


class AccountListView(ListView):
    model = Account
    template_name = "finance/account_list.html"
    context_object_name = "accounts"
    paginate_by = 20

    def get_queryset(self):
        queryset = super().get_queryset().select_related("owner", "currency", "manager")
        currency_symbol = self.request.GET.get("currency")
        if currency_symbol:
            queryset = queryset.filter(currency__symbol=currency_symbol)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        decorated = PageProcessor().decorate(context, self.request)
        theme = decorated.get("theme", {}).get("colors", {})
        context["chart_colors"] = get_chart_colors(theme)

        context["currencies"] = Currency.objects.filter(active=True)
        context["selected_currency"] = self.request.GET.get("currency", "")
        return context



class AccountDetailView(DetailView):
    model = Account
    template_name = "finance/account_details.html"
    context_object_name = "account"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        account = self.object

        # Get selected period from query params
        period_days = int(self.request.GET.get("period", 30))
        since = now() - timedelta(days=period_days)

        # Filter by period
        context["transactions_outgoing"] = account.transaction_outgoing.filter(timestamp__gte=since)
        context["transactions_incoming"] = account.transaction_incoming.filter(timestamp__gte=since)
        context["obligations_outgoing"] = account.obligation_outgoing.filter(timestamp__gte=since)
        context["obligations_incoming"] = account.obligation_incoming.filter(timestamp__gte=since)

        context["selected_period"] = period_days
        context["available_periods"] = [7, 30, 90, 180, 365]

        decorated = PageProcessor().decorate(context, self.request)
        theme = decorated.get("theme", {}).get("colors", {})
        context["chart_colors"] = get_chart_colors(theme)
        return context

