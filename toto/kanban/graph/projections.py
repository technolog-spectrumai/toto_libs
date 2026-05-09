from toto.kanban.models import Campaign, Column, Mission, Project, Sprint, Task
from toto.kanban.graph.models import (
    CampaignNode,
    ColumnNode,
    MissionNode,
    ProjectNode,
    SprintNode,
    TaskNode,
)
from toto.socialhub.graph.models import Person as PersonNode


class BaseKanbanProjection:
    app = "kanban"
    model = None
    sql_model = None
    neo_model = None
    field_map = {}

    def as_string(self, value):
        return str(value) if value is not None else None

    def item_count(self):
        return self.sql_model.objects.count()

    def link_count(self):
        return 0

    def node_data_size(self):
        return len(self.field_map)

    def projection_stats(self):
        return {
            "items": self.item_count(),
            "links": self.link_count(),
            "node_data_size": self.node_data_size(),
        }

    def sync_nodes(self):
        for obj in self.sql_model.objects.all():
            node = self.neo_model.nodes.get_or_none(uuid=str(obj.uid))

            if node and type(node) is not self.neo_model:
                node.delete()
                node = None

            if not node:
                node = self.neo_model(uuid=str(obj.uid))

            for neo_field, sql_field in self.field_map.items():
                value = getattr(obj, sql_field)
                if neo_field in {"start_date", "end_date", "due_date"}:
                    value = self.as_string(value)
                elif neo_field == "metadata":
                    value = value or {}
                setattr(node, neo_field, value)

            node.save()

    def sync_edges(self):
        pass


class ProjectProjection(BaseKanbanProjection):
    model = "Project"
    sql_model = Project
    neo_model = ProjectNode
    field_map = {
        "name": "name",
        "description": "description",
    }

    def sync_edges(self):
        for project in Project.objects.select_related("owner"):
            node = ProjectNode.nodes.get(uuid=str(project.uid))
            node.owner.disconnect_all()

            owner = PersonNode.nodes.get_or_none(uuid=str(project.owner.uid))
            if owner:
                node.owner.connect(owner)

    def link_count(self):
        return Project.objects.filter(owner__isnull=False).count()


class ColumnProjection(BaseKanbanProjection):
    model = "Column"
    sql_model = Column
    neo_model = ColumnNode
    field_map = {
        "name": "name",
        "position": "position",
        "can_add_task": "can_add_task",
    }

    def sync_edges(self):
        for column in Column.objects.select_related("project"):
            node = ColumnNode.nodes.get(uuid=str(column.uid))
            node.project.disconnect_all()

            project = ProjectNode.nodes.get_or_none(uuid=str(column.project.uid))
            if project:
                node.project.connect(project)

    def link_count(self):
        return Column.objects.filter(project__isnull=False).count()


class CampaignProjection(BaseKanbanProjection):
    model = "Campaign"
    sql_model = Campaign
    neo_model = CampaignNode
    field_map = {
        "name": "name",
        "description": "description",
        "start_date": "start_date",
        "end_date": "end_date",
        "metadata": "metadata",
    }

    def sync_edges(self):
        for campaign in Campaign.objects.select_related("project", "owner"):
            node = CampaignNode.nodes.get(uuid=str(campaign.uid))
            node.project.disconnect_all()
            node.owner.disconnect_all()

            project = ProjectNode.nodes.get_or_none(uuid=str(campaign.project.uid))
            if project:
                node.project.connect(project)

            if campaign.owner_id:
                owner = PersonNode.nodes.get_or_none(uuid=str(campaign.owner.uid))
                if owner:
                    node.owner.connect(owner)

    def link_count(self):
        return (
            Campaign.objects.filter(project__isnull=False).count()
            + Campaign.objects.filter(owner__isnull=False).count()
        )


class MissionProjection(BaseKanbanProjection):
    model = "Mission"
    sql_model = Mission
    neo_model = MissionNode
    field_map = {
        "title": "title",
        "description": "description",
        "urgency": "urgency",
        "impact": "impact",
        "metadata": "metadata",
    }

    def sync_edges(self):
        for mission in Mission.objects.select_related("campaign", "owner"):
            node = MissionNode.nodes.get(uuid=str(mission.uid))
            node.campaign.disconnect_all()
            node.owner.disconnect_all()

            campaign = CampaignNode.nodes.get_or_none(uuid=str(mission.campaign.uid))
            if campaign:
                node.campaign.connect(campaign)

            if mission.owner_id:
                owner = PersonNode.nodes.get_or_none(uuid=str(mission.owner.uid))
                if owner:
                    node.owner.connect(owner)

    def link_count(self):
        return (
            Mission.objects.filter(campaign__isnull=False).count()
            + Mission.objects.filter(owner__isnull=False).count()
        )


class SprintProjection(BaseKanbanProjection):
    model = "Sprint"
    sql_model = Sprint
    neo_model = SprintNode
    field_map = {
        "name": "name",
        "start_time": "start_time",
        "end_time": "end_time",
    }

    def sync_edges(self):
        for sprint in Sprint.objects.select_related("project"):
            node = SprintNode.nodes.get(uuid=str(sprint.uid))
            node.project.disconnect_all()

            project = ProjectNode.nodes.get_or_none(uuid=str(sprint.project.uid))
            if project:
                node.project.connect(project)

    def link_count(self):
        return Sprint.objects.filter(project__isnull=False).count()


class TaskProjection(BaseKanbanProjection):
    model = "Task"
    sql_model = Task
    neo_model = TaskNode
    field_map = {
        "title": "title",
        "description": "description",
        "due_date": "due_date",
        "position": "position",
        "weight": "weight",
        "metadata": "metadata",
        "completed_at": "completed_at",
    }

    def sync_edges(self):
        for task in Task.objects.select_related("mission", "column", "sprint", "assignee"):
            node = TaskNode.nodes.get(uuid=str(task.uid))
            node.mission.disconnect_all()
            node.column.disconnect_all()
            node.sprint.disconnect_all()
            node.assignee.disconnect_all()

            mission = MissionNode.nodes.get_or_none(uuid=str(task.mission.uid))
            if mission:
                node.mission.connect(mission)

            column = ColumnNode.nodes.get_or_none(uuid=str(task.column.uid))
            if column:
                node.column.connect(column)

            if task.sprint_id:
                sprint = SprintNode.nodes.get_or_none(uuid=str(task.sprint.uid))
                if sprint:
                    node.sprint.connect(sprint)

            if task.assignee_id:
                assignee = PersonNode.nodes.get_or_none(uuid=str(task.assignee.uid))
                if assignee:
                    node.assignee.connect(assignee)

    def link_count(self):
        return (
            Task.objects.filter(mission__isnull=False).count()
            + Task.objects.filter(column__isnull=False).count()
            + Task.objects.filter(sprint__isnull=False).count()
            + Task.objects.filter(assignee__isnull=False).count()
        )
