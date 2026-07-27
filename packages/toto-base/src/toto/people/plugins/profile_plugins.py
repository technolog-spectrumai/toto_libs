from toto.socialhub.plugins.profile_plugins import ProfilePlugin
from toto.people.civic import is_committed_citizen, get_signed_constitutions


@ProfilePlugin.plugin(key="civic_status", title="Civic Status", order=15)
class CivicStatusPlugin(ProfilePlugin):
    template_name = "people/profile_plugins/civic_status.html"
    section_icon = "fa-solid fa-id-card-clip"

    def is_visible_for_profile(self, **kwargs) -> bool:
        profile = kwargs.get("profile")
        if not profile:
            return False
        # Show if person has any civic activity or is a federal agent
        return (
            is_committed_citizen(profile)
            or getattr(profile, "is_federal_agent", False)
        )

    def get_context(self, **kwargs):
        context = super().get_context(**kwargs)
        profile = kwargs.get("profile")
        committed = is_committed_citizen(profile) if profile else False
        signed = list(get_signed_constitutions(profile)) if profile else []
        context.update({
            "civic_profile": profile,
            "is_committed_citizen": committed,
            "is_federal_agent": getattr(profile, "is_federal_agent", False),
            "signed_constitutions": signed,
        })
        return context
