"""Home on the map, on one's own profile (2026-10-04).

The map picker, the place search and the three-way sharing setting were the
"Where you live" section of the socialhub's Edit profile tab. toto-base
carries no geography now — a person's address there is text — so on a host
that installs this app they arrive as this plugin, on the owner's Overview,
posting to this app's own doors (``home_views``).
"""

from django.utils.translation import gettext_lazy as _

from toto.socialhub.plugins.profile_plugins import ProfilePlugin


@ProfilePlugin.plugin(
    key="home_on_the_map",
    title=_("Where you live"),
    order=20,
)
class HomeOnTheMapProfilePlugin(ProfilePlugin):
    template_name = "locations/profile_plugins/home.html"
    section_icon = "fa-solid fa-map-pin"
    show_for_owner_only = True
    tab = "overview"

    def get_context(self, **kwargs):
        from toto.locations.geocode import geocoding_enabled, geocoding_settings
        from toto.locations.models import HomeSharing
        from toto.locations.people_access import home_address, sharing_of

        context = super().get_context(**kwargs)
        profile = kwargs["profile"]
        context["profile"] = profile
        context["home_address"] = home_address(profile)
        context["home_sharing"] = sharing_of(profile)
        context["sharing_choices"] = HomeSharing.choices
        context["geocoding_enabled"] = geocoding_enabled(geocoding_settings())
        return context
