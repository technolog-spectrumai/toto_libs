from toto.kanban.plugins.mission_plugins import MissionPlugin
from toto.kanban.plugins.task_plugins import TaskPlugin

from ..models import Detection


@MissionPlugin.plugin(
    key="detections",
    title="Detections",
    order=30,
)
class MissionDetectionsPlugin(MissionPlugin):
    template_name = "detections/kanban_plugins/mission_detections.html"
    section_icon = "fa-solid fa-triangle-exclamation"

    def is_visible_for_mission(self, **kwargs) -> bool:
        mission = kwargs["mission"]
        return Detection.objects.filter(mitigation_task__mission=mission).exists()

    def get_context(self, **kwargs):
        context = super().get_context(**kwargs)
        mission = kwargs["mission"]
        context["mission_detections"] = Detection.objects.filter(
            mitigation_task__mission=mission,
        ).select_related(
            "category",
            "address",
            "mitigation_task",
            "mitigation_task__column",
        ).order_by("-start_time")[:20]
        return context


@TaskPlugin.plugin(
    key="detections",
    title="Detections",
    order=30,
)
class TaskDetectionsPlugin(TaskPlugin):
    template_name = "detections/kanban_plugins/task_detections.html"
    section_icon = "fa-solid fa-triangle-exclamation"

    def is_visible_for_task(self, **kwargs) -> bool:
        task = kwargs["task"]
        return Detection.objects.filter(mitigation_task=task).exists()

    def get_context(self, **kwargs):
        context = super().get_context(**kwargs)
        task = kwargs["task"]
        context["task_detections"] = Detection.objects.filter(
            mitigation_task=task,
        ).select_related(
            "category",
            "address",
        ).order_by("-start_time")
        return context
