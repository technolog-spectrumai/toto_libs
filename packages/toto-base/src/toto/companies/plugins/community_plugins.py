"""A company on its community page (stage 65).

Two sections, both only for a community whose kind is ``company`` and only
for a signed-in viewer (``access.may_see_register``), so an ordinary
community's page holds neither and a visitor sees neither:

``company_identity``
    on the Overview: the company's ID number, and for who may manage the
    form that sets it;
``company_shareholdings``
    on a tab of its own, Shareholdings: holder, quantity, percentage, the
    total recorded, the basis of the percentages in words, and for who may
    manage the forms that record, change and remove a holding.
"""

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from toto.socialhub.plugins.community_plugins import CommunityPlugin


class _CompanyPlugin(CommunityPlugin):
    """What the two sections share: who is asking, and the rule."""

    @classmethod
    def should_register(cls) -> bool:
        return cls is not _CompanyPlugin

    @staticmethod
    def _viewer(kwargs):
        return getattr(kwargs.get("request"), "user", None)

    def is_visible(self, **kwargs) -> bool:
        from toto.companies.access import may_see_register

        if not super().is_visible(**kwargs):
            return False
        return may_see_register(self._viewer(kwargs), self.get_community_from_kwargs(**kwargs))

    def get_context(self, **kwargs):
        from toto.companies.access import may_manage

        context = super().get_context(**kwargs)
        community = self.get_community_from_kwargs(**kwargs)
        context["company_may_manage"] = may_manage(self._viewer(kwargs), community)
        return context


@CommunityPlugin.plugin(key="company_identity", title=_("Company"), order=5)
class CompanyIdentityPlugin(_CompanyPlugin):
    template_name = "companies/plugins/identity.html"
    section_icon = "fa-solid fa-building"

    def get_context(self, **kwargs):
        from toto.companies.models import ID_NUMBER_MAX, CompanyRecord

        context = super().get_context(**kwargs)
        community = self.get_community_from_kwargs(**kwargs)
        record = CompanyRecord.objects.filter(community=community).first()
        context["company_id_number"] = record.id_number if record is not None else ""
        context["company_id_number_max"] = ID_NUMBER_MAX
        context["company_number_url"] = reverse("companies:number",
                                                kwargs={"slug": community.slug})
        return context


@CommunityPlugin.plugin(key="company_shareholdings", title=_("Shareholdings"), order=50)
class ShareholdingsPlugin(_CompanyPlugin):
    template_name = "companies/plugins/shareholdings.html"
    section_icon = "fa-solid fa-chart-pie"
    tab = "shareholdings"

    def get_context(self, **kwargs):
        from toto.companies.register import register_of

        context = super().get_context(**kwargs)
        community = self.get_community_from_kwargs(**kwargs)
        slug = {"slug": community.slug}
        context["company_register"] = register_of(community)
        context["company_holding_url"] = reverse("companies:holding_save", kwargs=slug)
        if context["company_may_manage"]:
            from toto.people.models import Person

            # Every person, by name: a holder need not be a member.
            context["company_people"] = Person.objects.order_by("display_name", "pk")
        return context
