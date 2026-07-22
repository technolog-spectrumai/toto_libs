from __future__ import annotations

from typing import ClassVar

from django.urls import reverse

from toto.core.plugin import BasePlugin


class MissionTabPlugin(BasePlugin):
    """
    Registers a navigation tab on the mission detail page.

    External apps (e.g. mission_economy) subclass this and register via
    @MissionTabPlugin.plugin(...) to add tabs without kanban knowing about them.
    """

    registry: ClassVar[dict[str, "MissionTabPlugin"]] = {}

    tab_icon: ClassVar[str] = "fa-solid fa-puzzle-piece"
    url_name: ClassVar[str] = ""

    def get_tab_url(self, mission) -> str:
        return reverse(self.url_name, kwargs={"pk": mission.pk})

    def is_visible_for_mission(self, **kwargs) -> bool:
        return True

    def is_visible(self, **kwargs) -> bool:
        if not super().is_visible(**kwargs):
            return False
        return self.is_visible_for_mission(**kwargs)
