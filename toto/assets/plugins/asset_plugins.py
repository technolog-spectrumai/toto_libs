from typing import Any, ClassVar

from toto.assets.models import TokenizationDefaultReason
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


@AssetPlugin.plugin(
    key="asset_underlying_objects",
    title="Underlying Objects",
    order=20,
)
class UnderlyingObjectsAssetPlugin(AssetPlugin):
    template_name = "assets/plugins/asset_underlying_objects.html"
    section_icon = "fa-solid fa-cube"

    def get_context(self, **kwargs) -> dict[str, Any]:
        context = super().get_context(**kwargs)
        asset = self.get_asset_from_kwargs(**kwargs)
        context["tokenizations"] = (
            asset.tokenizations
            .select_related("real_world_object", "supervisor")
            .order_by("-created_at")
        )
        context["tokenization_default_reasons"] = TokenizationDefaultReason.choices
        from django.apps import apps
        if apps.is_installed("toto.mission_economy"):
            from toto.mission_economy.models import ProjectTokenization
            context["project_tokenization"] = (
                ProjectTokenization.objects
                .select_related("project", "supervisor")
                .filter(asset=asset)
                .first()
            )
        return context
