from typing import Any, ClassVar

from toto.core.plugin import BasePlugin


class TaskPlugin(BasePlugin):
    registry: ClassVar[dict[str, "TaskPlugin"]] = {}
    section_icon: ClassVar[str] = "fa-solid fa-puzzle-piece"

    @staticmethod
    def get_task_from_kwargs(**kwargs):
        return kwargs.get("task")

    def is_visible_for_task(self, **kwargs) -> bool:
        return True

    def is_visible(self, **kwargs) -> bool:
        if not super().is_visible(**kwargs):
            return False
        if self.get_task_from_kwargs(**kwargs) is None:
            return False
        return self.is_visible_for_task(**kwargs)

    def get_context(self, **kwargs) -> dict[str, Any]:
        context = super().get_context(**kwargs)
        context.update({
            "task_plugin": self,
            "task_plugin_key": self.get_key(),
            "task_plugin_title": self.get_title(),
            "task_plugin_icon": self.section_icon,
        })
        return context
