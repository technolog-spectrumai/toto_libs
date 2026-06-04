"""
Registers the Assembly section into the SocialHub CommunityPlugin registry.
Imported by AssemblyConfig.ready() — socialhub never imports this module.
"""
from toto.socialhub.plugins.community_plugins import CommunityPlugin


@CommunityPlugin.plugin(key="assembly", title="Assembly", order=50)
class AssemblyCommunityPlugin(CommunityPlugin):
    section_icon = "fa-solid fa-landmark"
    template_name = "assembly/community_card.html"

    def is_visible(self, **kwargs) -> bool:
        if not super().is_visible(**kwargs):
            return False
        request = kwargs.get("request")
        community = kwargs.get("community")
        if not request or not community:
            return False
        # Only visible to authenticated members
        if not request.user.is_authenticated:
            return False
        person = getattr(request.user, "community_profile", None)
        return bool(person and community.members.filter(pk=person.pk).exists())

    def get_context(self, **kwargs) -> dict:
        context = super().get_context(**kwargs)
        community = kwargs.get("community")
        request = kwargs.get("request")
        person = getattr(request.user, "community_profile", None) if request else None

        from .models import AssemblyProposal, AssemblyStatus, CommunityRule, CommunityTransactionFee, PollTax, PollTaxPayment

        open_proposals = AssemblyProposal.objects.filter(
            community=community,
            status=AssemblyStatus.OPEN,
        ).count()

        # Voting rights: exempt, no active poll tax, or all paid
        has_voting_rights = True
        unpaid_taxes = []
        from toto.people.civic import is_committed_citizen
        is_exempt = (
            person
            and (
                is_committed_citizen(person)
                or person.communities.filter(is_federal_tribe=True).exists()
            )
        )
        if person and not is_exempt:
            active_taxes = PollTax.objects.filter(community=community, active=True)
            for pt in active_taxes:
                label = pt.current_period_label()
                if not PollTaxPayment.objects.filter(poll_tax=pt, person=person, period_label=label).exists():
                    has_voting_rights = False
                    unpaid_taxes.append(pt)

        context.update({
            "assembly_community": community,
            "assembly_open_proposals": open_proposals,
            "assembly_rules_count": CommunityRule.objects.filter(community=community, active=True).count(),
            "assembly_fees_count": CommunityTransactionFee.objects.filter(community=community, active=True).count(),
            "assembly_has_voting_rights": has_voting_rights,
            "assembly_unpaid_taxes": unpaid_taxes,
            "assembly_person": person,
        })
        return context
