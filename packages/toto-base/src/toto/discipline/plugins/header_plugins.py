"""The mana chip in the app bar: a bar and the one number, on every page,
for everybody signed in. The bar is the balance against the cap. It is
``pinned``: when the bar folds into its menu on a narrow screen the chip
stays beside the brand (2026-10-12)."""

from toto.core.plugin import HeaderPlugin


@HeaderPlugin.plugin(key="discipline_chip", order=10)
class ManaChipPlugin(HeaderPlugin):
    template_name = "discipline/plugins/_chip.html"
    pinned = True

    def visible_for_request(self, request):
        user = getattr(request, "user", None)
        return bool(user is not None and getattr(user, "is_authenticated", False))

    def get_context(self, **kwargs):
        from django.urls import NoReverseMatch, reverse

        from .. import services

        request = kwargs.get("request")
        # One read a page, shared by the bar's and the dropdown's renders.
        state = getattr(request, "_toto_discipline_chip", None)
        if state is None:
            state = services.chip_state(request.user)
            request._toto_discipline_chip = state
        try:
            url = reverse("discipline:index")
        except NoReverseMatch:
            url = ""
        return {"mana_amount": state["amount"], "mana_cap": state["cap"],
                "mana_pct": state["pct"], "mana_url": url,
                "variant": kwargs.get("variant", "bar")}

    def render_html(self, **kwargs):
        from django.template.loader import render_to_string

        context = self.get_context(**kwargs)
        if not context["mana_url"]:
            return ""            # the host does not mount the pages: no chip
        return render_to_string(self.template_name, context, request=kwargs.get("request"))
