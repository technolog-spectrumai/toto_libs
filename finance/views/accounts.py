from django.utils.timezone import now
from django.views.generic import DetailView
from finance.models import Account, Currency
from oya.page import PageProcessor
from django.db.models import Sum
from datetime import timedelta



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

