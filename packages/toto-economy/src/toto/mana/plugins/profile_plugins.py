"""The member's own pools, on their own profile page."""

from toto.socialhub.plugins.profile_plugins import ProfilePlugin


@ProfilePlugin.plugin(key="mana", title="Mana", order=15)
class ManaProfilePlugin(ProfilePlugin):
    template_name = "mana/profile_plugins/mana.html"
    section_icon = "fa-solid fa-droplet"
    show_for_owner_only = True

    def is_visible_for_profile(self, **kwargs) -> bool:
        profile = kwargs.get("profile")
        return bool(profile and getattr(profile, "user", None))

    def get_context(self, **kwargs):
        from .. import services

        context = super().get_context(**kwargs)
        user = getattr(kwargs.get("profile"), "user", None)
        balances = services.balances_of(user) or {}
        context["mana_balances"] = [balances[r] for r in ("security", "compute", "storage")
                                    if r in balances]
        return context
