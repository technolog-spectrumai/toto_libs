from typing import Any, ClassVar

from toto.core.plugin import BasePlugin


class MissionPlugin(BasePlugin):
    registry: ClassVar[dict[str, "MissionPlugin"]] = {}
    section_icon: ClassVar[str] = "fa-solid fa-puzzle-piece"

    @staticmethod
    def get_mission_from_kwargs(**kwargs):
        return kwargs.get("mission")

    def is_visible_for_mission(self, **kwargs) -> bool:
        return True

    def is_visible(self, **kwargs) -> bool:
        if not super().is_visible(**kwargs):
            return False
        if self.get_mission_from_kwargs(**kwargs) is None:
            return False
        return self.is_visible_for_mission(**kwargs)

    def get_context(self, **kwargs) -> dict[str, Any]:
        context = super().get_context(**kwargs)
        context.update({
            "mission_plugin": self,
            "mission_plugin_key": self.get_key(),
            "mission_plugin_title": self.get_title(),
            "mission_plugin_icon": self.section_icon,
        })
        return context
