from typing import Any, ClassVar

from toto.core.plugin import BasePlugin


class ProfilePlugin(BasePlugin):
    """
    Base class for plugins rendered on Person profile pages.
    """

    registry: ClassVar[dict[str, "ProfilePlugin"]] = {}

    section_icon: ClassVar[str] = "fa-solid fa-puzzle-piece"
    show_for_owner_only: ClassVar[bool] = False

    @staticmethod
    def get_profile_from_kwargs(**kwargs):
        return kwargs.get("profile")

    @staticmethod
    def get_request_from_kwargs(**kwargs):
        return kwargs.get("request")

    def is_profile_owner(self, **kwargs) -> bool:
        request = self.get_request_from_kwargs(**kwargs)
        profile = self.get_profile_from_kwargs(**kwargs)

        return bool(
            request
            and request.user.is_authenticated
            and profile
            and profile.user == request.user
        )

    def is_visible_for_profile(self, **kwargs) -> bool:
        return True

    def is_visible(self, **kwargs) -> bool:
        if not super().is_visible(**kwargs):
            return False

        profile = self.get_profile_from_kwargs(**kwargs)

        if profile is None:
            return False

        if self.show_for_owner_only and not self.is_profile_owner(**kwargs):
            return False

        return self.is_visible_for_profile(**kwargs)

    def get_context(self, **kwargs) -> dict[str, Any]:
        context = super().get_context(**kwargs)

        context.update(
            {
                "profile_plugin": self,
                "profile_plugin_key": self.get_key(),
                "profile_plugin_title": self.get_title(),
                "profile_plugin_icon": self.section_icon,
            }
        )

        return context
