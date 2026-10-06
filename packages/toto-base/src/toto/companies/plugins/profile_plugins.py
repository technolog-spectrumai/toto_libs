"""A person's shareholdings on their profile (stage 65).

On the Overview, key ``company_shareholdings``, titled "Company
shareholdings": the company with a link to its page, the quantity and the
percentage of that company's recorded shares. Only holdings the viewer may
see (``register.holdings_of``: in communities that are companies, for a
signed-in viewer, the profile page's own rule); with none to show the
section is not drawn at all, for the profile's owner too.

The profile's code knows nothing of companies: this hangs on
``ProfilePlugin`` and is absent on a host without the app.
"""

from django.utils.translation import gettext_lazy as _

from toto.socialhub.plugins.profile_plugins import ProfilePlugin


@ProfilePlugin.plugin(key="company_shareholdings", title=_("Company shareholdings"),
                      order=30)
class ShareholdingsProfilePlugin(ProfilePlugin):
    template_name = "companies/plugins/profile_holdings.html"
    section_icon = "fa-solid fa-chart-pie"
    tab = "overview"

    def _rows(self, **kwargs):
        from toto.companies.register import holdings_of

        request = self.get_request_from_kwargs(**kwargs)
        return holdings_of(self.get_profile_from_kwargs(**kwargs),
                           getattr(request, "user", None))

    def is_visible_for_profile(self, **kwargs) -> bool:
        return bool(self._rows(**kwargs))

    def get_context(self, **kwargs):
        context = super().get_context(**kwargs)
        context["company_holdings"] = self._rows(**kwargs)
        return context
