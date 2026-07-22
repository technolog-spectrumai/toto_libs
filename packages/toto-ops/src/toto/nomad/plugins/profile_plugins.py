from toto.socialhub.plugins.profile_plugins import ProfilePlugin

from toto.nomad import service


@ProfilePlugin.plugin(key="nomad_onion", title="Onion Identity", order=90)
class OnionIdentityPlugin(ProfilePlugin):
    """Settings card showing faros's current .onion + a superuser "Migrate" button.

    Only rendered on a superuser's own profile/settings page — migrating the
    server's onion is a server-wide operation.
    """

    template_name = "nomad/profile_plugins/onion_identity.html"
    section_icon = "fa-solid fa-mask"
    show_for_owner_only = True

    def is_visible_for_profile(self, **kwargs) -> bool:
        request = self.get_request_from_kwargs(**kwargs)
        return bool(
            request
            and request.user.is_authenticated
            and request.user.is_superuser
        )

    def get_context(self, **kwargs):
        context = super().get_context(**kwargs)
        request = self.get_request_from_kwargs(**kwargs)
        context["current_onion"] = service.current_onion()
        context["reachability"] = service.reachability()
        # The transport the viewer is currently connected over (stamped by nginx),
        # so the template can warn before they disable the one they're on.
        context["current_transport"] = (
            request.META.get("HTTP_X_FAROS_TRANSPORT") if request else None
        )
        return context
