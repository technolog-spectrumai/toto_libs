from typing import Any, ClassVar

from toto.core.plugin import BasePlugin


class InventoryObjectPlugin(BasePlugin):
    """
    Base class for sections injected into Inventory object detail pages.
    """

    registry: ClassVar[dict[str, "InventoryObjectPlugin"]] = {}
    section_icon: ClassVar[str] = "fa-solid fa-puzzle-piece"

    @staticmethod
    def get_inventory_object_from_kwargs(**kwargs):
        return kwargs.get("inventory_object") or kwargs.get("object")

    def is_visible(self, **kwargs) -> bool:
        if not super().is_visible(**kwargs):
            return False
        return self.get_inventory_object_from_kwargs(**kwargs) is not None

    def get_context(self, **kwargs) -> dict[str, Any]:
        context = super().get_context(**kwargs)
        context.update(
            {
                "inventory_object_plugin": self,
                "inventory_object_plugin_key": self.get_key(),
                "inventory_object_plugin_title": self.get_title(),
                "inventory_object_plugin_icon": self.section_icon,
            }
        )
        return context
