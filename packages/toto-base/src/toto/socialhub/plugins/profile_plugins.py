from typing import Any, ClassVar

from toto.core.plugin import BasePlugin, RenderedPlugin

#: The tabs of a profile a plugin's section may sit on (2026-10-02, stage 50:
#: the profile split into tabs). "overview" is the page others see, and the
#: default; "wallet" is the owner's own — it is never on anybody else's
#: profile, whatever a plugin allows; "activity" is the owner's and the
#: visitors', each plugin by its own rules. Any other name is the overview.
PLUGIN_TABS = ("overview", "wallet", "activity")


class ProfilePlugin(BasePlugin):
    """
    Base class for plugins rendered on Person profile pages.

    ``tab`` names the tab of the profile the section is on (``PLUGIN_TABS``):
    the page draws the active tab's plugins alone, so a plugin on another tab
    is not asked anything, and a tab whose plugins all decline to show is
    left off the strip (``shows_on_tab``).
    """

    registry: ClassVar[dict[str, "ProfilePlugin"]] = {}

    section_icon: ClassVar[str] = "fa-solid fa-puzzle-piece"
    show_for_owner_only: ClassVar[bool] = False
    tab: ClassVar[str] = "overview"

    @classmethod
    def get_tab(cls) -> str:
        return cls.tab if cls.tab in PLUGIN_TABS else "overview"

    @classmethod
    def on_tab(cls, tab: str) -> list["ProfilePlugin"]:
        """The plugins whose section is on ``tab``, in their order."""
        return [plugin for plugin in cls.all() if plugin.get_tab() == tab]

    @classmethod
    def render_tab(cls, tab: str, **kwargs) -> list[RenderedPlugin]:
        """``render_all`` for one tab: the other tabs' plugins are not asked."""
        rendered = []
        for plugin in cls.on_tab(tab):
            result = plugin.render(**kwargs)
            if result is not None:
                rendered.append(result)
        return sorted(rendered)

    @classmethod
    def shows_on_tab(cls, tab: str, **kwargs) -> bool:
        """Would any plugin on ``tab`` show for this request — each by its own
        ``is_visible``, nothing rendered."""
        return any(plugin.is_visible(**kwargs) for plugin in cls.on_tab(tab))

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
