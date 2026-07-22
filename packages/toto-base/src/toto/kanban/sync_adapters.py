from toto.noosphere.adapters import BaseSyncAdapter
from toto.noosphere.registry import register_sync_adapter

from .models import Project, Campaign, Mission, Sprint, Task


@register_sync_adapter
class ProjectSyncAdapter(BaseSyncAdapter):
    model = Project
    allowed_fields = [
        "name",
        "description",
    ]


@register_sync_adapter
class CampaignSyncAdapter(BaseSyncAdapter):
    model = Campaign
    allowed_fields = [
        "name",
        "description",
        "start_date",
        "end_date",
        "metadata",
    ]


@register_sync_adapter
class MissionSyncAdapter(BaseSyncAdapter):
    model = Mission
    allowed_fields = [
        "title",
        "description",
        "urgency",
        "impact",
        "metadata",
    ]


@register_sync_adapter
class SprintSyncAdapter(BaseSyncAdapter):
    model = Sprint
    allowed_fields = [
        "name",
        "start_time",
        "end_time",
    ]


@register_sync_adapter
class TaskSyncAdapter(BaseSyncAdapter):
    model = Task
    allowed_fields = [
        "title",
        "description",
        "due_date",
        "position",
        "weight",
        "metadata",
        "completed_at",
    ]
