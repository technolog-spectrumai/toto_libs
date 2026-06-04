from typing import Any

from toto.assets.models import TokenizationDefaultReason
from toto.inventory.plugins.inventory_object_plugins import InventoryObjectPlugin


@InventoryObjectPlugin.plugin(
    key="asset_tokenization",
    title="Tokenization",
    order=30,
)
class AssetTokenizationInventoryObjectPlugin(InventoryObjectPlugin):
    template_name = "assets/plugins/inventory_object_tokenization.html"
    section_icon = "fa-solid fa-coins"

    def get_context(self, **kwargs) -> dict[str, Any]:
        context = super().get_context(**kwargs)
        obj = self.get_inventory_object_from_kwargs(**kwargs)
        tokenization = (
            obj.tokenizations
            .select_related("asset", "supervisor")
            .order_by("-created_at")
            .first()
        )
        context.update(
            {
                "tokenization": tokenization,
                "can_tokenize": tokenization is None,
                "tokenization_default_reasons": TokenizationDefaultReason.choices,
            }
        )
        return context
