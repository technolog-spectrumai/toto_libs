from toto.socialhub.plugins.profile_plugins import ProfilePlugin


@ProfilePlugin.plugin(
    key="magistrate_seats",
    title="Magistrate Command Console",
    order=18,
)
class MagistrateSeatsPlugin(ProfilePlugin):
    template_name = "magistrate/profile_plugins/my_seats.html"
    section_icon = "fa-solid fa-building-columns"
    show_for_owner_only = True

    def is_visible_for_profile(self, **kwargs) -> bool:
        profile = kwargs.get("profile")
        if not profile:
            return False
        try:
            from toto.magistrate.models import Magistrate
            return Magistrate.objects.filter(person=profile, status="active").exists()
        except Exception:
            return False

    def get_context(self, **kwargs):
        context = super().get_context(**kwargs)
        profile = kwargs.get("profile")
        try:
            from toto.magistrate.models import Magistrate
            seats = list(
                Magistrate.objects
                .filter(person=profile, status="active")
                .select_related("role", "community")
                .order_by("role__order")
            )
        except Exception:
            seats = []
        context["active_seats"] = seats
        return context
