from django.utils.timezone import now
from django.views.generic import ListView, DetailView
from finance.models import Account, Currency
from oya.page import PageProcessor
from django.db.models import Sum
from datetime import timedelta


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

        decorated["currencies"] = Currency.objects.filter(active=True)
        decorated["selected_currency"] = self.request.GET.get("currency", "")
        return decorated


class AccountDetailView(DetailView):
    model = Account
    template_name = "finance/account_details.html"
    context_object_name = "account"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        account = self.object

        period_days = int(self.request.GET.get("period", 30))
        since = now() - timedelta(days=period_days)

        # Filter transactions
        incoming = account.transaction_incoming.filter(timestamp__gte=since)
        outgoing = account.transaction_outgoing.filter(timestamp__gte=since)

        context["transactions_outgoing"] = outgoing
        context["transactions_incoming"] = incoming

        # ---- BALANCE HISTORY ----

        # Start from the balance BEFORE the period
        initial_incoming = account.transaction_incoming.filter(timestamp__lt=since).aggregate(total=Sum("amount"))[
                               "total"] or 0
        initial_outgoing = account.transaction_outgoing.filter(timestamp__lt=since).aggregate(total=Sum("amount"))[
                               "total"] or 0
        starting_balance = initial_incoming - initial_outgoing

        running_balance = starting_balance

        dates = []
        balances = []

        for i in range(period_days + 1):
            day = since + timedelta(days=i)
            next_day = day + timedelta(days=1)

            day_in = incoming.filter(timestamp__gte=day, timestamp__lt=next_day).aggregate(total=Sum("amount"))[
                         "total"] or 0
            day_out = outgoing.filter(timestamp__gte=day, timestamp__lt=next_day).aggregate(total=Sum("amount"))[
                          "total"] or 0

            running_balance += (day_in - day_out)

            days_ago = period_days - i
            dates.append(days_ago)
            balances.append(float(running_balance))

        context["balance_dates"] = dates
        context["balance_values"] = balances

        context["selected_period"] = period_days
        context["available_periods"] = [7, 30, 90, 180, 365]

        return PageProcessor().decorate(context, self.request)

