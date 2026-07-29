from typing import Any, ClassVar

from toto.core.plugin import BasePlugin


class AssetPlugin(BasePlugin):
    """
    Base class for sections injected into Assets asset detail pages.
    """

    registry: ClassVar[dict[str, "AssetPlugin"]] = {}
    section_icon: ClassVar[str] = "fa-solid fa-puzzle-piece"

    @staticmethod
    def get_asset_from_kwargs(**kwargs):
        return kwargs.get("asset")

    def is_visible(self, **kwargs) -> bool:
        if not super().is_visible(**kwargs):
            return False
        return self.get_asset_from_kwargs(**kwargs) is not None

    def get_context(self, **kwargs) -> dict[str, Any]:
        context = super().get_context(**kwargs)
        context.update(
            {
                "asset_plugin": self,
                "asset_plugin_key": self.get_key(),
                "asset_plugin_title": self.get_title(),
                "asset_plugin_icon": self.section_icon,
            }
        )
        return context
