"""L0 — the mana chip in the app bar, on every page, for every member."""

from toto.core.plugin import HeaderPlugin


@HeaderPlugin.plugin(key="mana_chip", order=10)
class ManaChipPlugin(HeaderPlugin):
    template_name = "mana/plugins/_chip.html"

    def visible_for_request(self, request):
        user = getattr(request, "user", None)
        return bool(user is not None and getattr(user, "is_authenticated", False))

    def get_context(self, **kwargs):
        from django.urls import reverse

        from .. import services
        from ..views import chip_status

        context = super().get_context(**kwargs)
        request = kwargs.get("request")
        # One ledger read per page, shared by the bar and mobile renders.
        balances = getattr(request, "_toto_mana_balances", None)
        if balances is None:
            balances = services.balances_of(request.user) or {}
            request._toto_mana_balances = balances
        context.update({
            "mana_balances": [balances[r] for r in ("security", "compute", "storage")
                              if r in balances],
            "mana_lowest": services.lowest(balances),
            "mana_status_json": chip_status(balances),
            "mana_url": reverse("mana:index"),
            "mana_api_url": reverse("mana:api_balances"),
            "variant": kwargs.get("variant", "bar"),
        })
        return context

    def render_html(self, **kwargs):
        context = self.get_context(**kwargs)
        if not context["mana_balances"]:
            return ""               # no pools on this host: no chip, not three empty bars
        from django.template.loader import render_to_string

        return render_to_string(self.template_name, context, request=kwargs.get("request"))
