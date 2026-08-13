"""The gas pump: what this app costs YOU, in the corner of the page.

One registration, and it appears on every heavy app and nowhere else. The
alternative — an include in each app's template — is fifteen edits, fifteen
chances to forget, and a template change every time an app starts metering
something. `visible_for_request` makes the same honesty check `{% quota_tab %}`
already makes: if this app declares no metrics, there is nothing to explain and
no icon.

**Everything in it is live.** No per-app prose to keep in step with the rate
card — the modal reads the registry, the rate card, the user's own limits and
the user's own ledger, so it cannot say a price that is not being charged.

It also has to read correctly where NOTHING is priced, which is how zenobia runs
today: a real, empty rate card. "Free" and "this server does not bill" are
different sentences and the template says whichever is true.
"""

from toto.core.plugin import FloatingPlugin


@FloatingPlugin.plugin(key="gas_pump", order=20)
class GasPumpPlugin(FloatingPlugin):
    """A pump in the corner of any app that meters something."""

    template_name = "quota/plugins/_gas_pump.html"

    def visible_for_request(self, request):
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return False
        return bool(self._metrics_for(request))

    # -- data ---------------------------------------------------------------

    @staticmethod
    def _app_label(request) -> str:
        match = getattr(request, "resolver_match", None)
        return (match.app_name if match else "") or ""

    def _metrics_for(self, request):
        from toto.quota.metrics import registry

        label = self._app_label(request)
        if not label:
            return []
        try:
            return list(registry.for_app(label))
        except Exception:  # noqa: BLE001 - a widget must never break a page
            return []

    def get_context(self, **kwargs):
        from django.urls import NoReverseMatch, reverse

        from toto.quota import api, rates
        from toto.quota.metrics import policy_model_for

        context = super().get_context(**kwargs)
        request = kwargs.get("request")
        if request is None:
            return context

        user = request.user
        label = self._app_label(request)
        metrics = self._metrics_for(request)

        # One rate-card read for the page, shared with {% price_hint %} — a
        # toolbar full of hints plus this pump is still one query.
        card = getattr(request, "_toto_rate_card", None)
        if card is None:
            card = rates.rate_card()
            request._toto_rate_card = card

        spend = rates.spend_by_metric(user) or {}
        policy_model = policy_model_for(label)

        rows = []
        for metric in metrics:
            price = card.get(metric.code)
            used = limit = pct = None
            if policy_model is not None:
                try:
                    policy = api.get_policy(policy_model, metric.code, user)
                    used = api.used(policy_model, metric.code, user,
                                    period=(policy.period if policy else "lifetime"))
                    limit = api.effective_limit(policy, user) if policy else None
                except Exception:  # noqa: BLE001
                    used = limit = None
            if limit:
                pct = min(100, int((used or 0) / limit * 100))
            rows.append({
                "metric": metric,
                "price": price,
                "used": used,
                "limit": limit,
                "pct": pct,
                "spent": spend.get(metric.code),
            })

        try:
            usage_url = reverse("quota:my_usage_app", args=[label])
        except NoReverseMatch:
            usage_url = ""

        context.update({
            "gas_app_label": label,
            "gas_rows": rows,
            "gas_balance": rates.balance_of(user),
            "gas_priced": bool(card),
            "gas_usage_url": usage_url,
            "gas_wallet_url": rates.wallet_url(),
        })
        return context
