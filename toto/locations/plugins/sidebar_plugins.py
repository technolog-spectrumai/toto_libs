from typing import ClassVar
from toto.core.plugin import BasePlugin


class LocationSidebarPlugin(BasePlugin):
    registry: ClassVar[dict[str, "LocationSidebarPlugin"]] = {}
    template_name: str = ""
    section_icon: str = ""

    def get_context(self, **kwargs) -> dict:
        context = super().get_context(**kwargs)
        context["sidebar_plugin"] = self
        context["sidebar_plugin_key"] = self.key
        context["sidebar_plugin_title"] = self.title
        context["sidebar_plugin_icon"] = self.section_icon
        return context

    def is_visible(self, **kwargs) -> bool:
        return True
