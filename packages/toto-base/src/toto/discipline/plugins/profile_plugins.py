"""A person's own mana, on their own profile: the balance against the cap,
how fast it refills and from what, the sigils they hold. Nobody else's
profile shows it."""

from django.utils.translation import gettext_lazy

from toto.socialhub.plugins.profile_plugins import ProfilePlugin


@ProfilePlugin.plugin(key="discipline_mana", title=gettext_lazy("Mana"), order=15)
class ManaProfilePlugin(ProfilePlugin):
    template_name = "discipline/plugins/profile_mana.html"
    section_icon = "fa-solid fa-bolt"
    show_for_owner_only = True
    tab = "overview"

    def is_visible_for_profile(self, **kwargs) -> bool:
        profile = kwargs.get("profile")
        return bool(profile and getattr(profile, "user_id", None))

    def get_context(self, **kwargs):
        from django.urls import NoReverseMatch, reverse

        from .. import services

        context = super().get_context(**kwargs)
        user = kwargs["profile"].user
        speed = services.speed_for(user)
        try:
            url = reverse("discipline:index")
        except NoReverseMatch:
            url = ""
        context.update({"mana": services.chip_state(user), "mana_speed": speed,
                        "mana_community": speed.community, "mana_url": url})
        return context
