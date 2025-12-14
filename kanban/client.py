from django.db.models import Sum, Q
from .models import Project, Column, Campaign, Mission, Sprint, Task


class KanbanClient:
    """
    Kanban client for querying core objects.
    Provides simple reusable methods returning querysets or dicts.
    """

    Q = Q
    Sum = Sum

    # --- Projects ---
    def get_project(self, project_id: int):
        return Project.objects.filter(id=project_id).first()

    def get_project_by_slug(self, slug: str):
        return Project.objects.filter(slug=slug).first()

    def all_projects(self):
        return Project.objects.all()

    # --- Columns ---
    def get_columns(self, project: Project):
        return Column.objects.filter(project=project).order_by("position")

    # --- Campaigns ---
    def get_campaigns(self, project: Project):
        return Campaign.objects.filter(project=project)

    # --- Missions ---
    def get_missions(self, campaign: Campaign):
        return campaign.missions.all()

    def get_missions_for_project(self, project: Project):
        return Mission.objects.filter(campaign__project=project)

    # --- Sprints ---
    def get_sprints(self, project: Project):
        return Sprint.objects.filter(project=project).order_by("start_time")

    def latest_sprint(self, project: Project):
        return Sprint.objects.filter(project=project).order_by("-start_time").first()

    # --- Tasks ---
    def get_tasks(self, project: Project):
        return Task.objects.filter(mission__campaign__project=project)

    def completed_tasks(self, project: Project):
        return self.get_tasks(project).filter(completed_at__isnull=False)

    def incomplete_tasks(self, project: Project):
        return self.get_tasks(project).filter(completed_at__isnull=True)

    # --- Simple metrics ---
    def sprint_velocity(self, sprint: Sprint):
        """Total completed weight in a sprint."""
        return sprint.tasks.filter(completed_at__isnull=False).aggregate(
            total=self.Sum("weight")
        )["total"] or 0

    def campaign_progress(self, campaign: Campaign):
        """Return progress percentage for a campaign."""
        total = campaign.missions.aggregate(total=self.Sum("tasks__weight"))["total"] or 0
        done = campaign.missions.aggregate(
            done=self.Sum("tasks__weight", filter=self.Q(tasks__completed_at__isnull=False))
        )["done"] or 0
        pct = (done / total * 100) if total > 0 else 0
        return {"label": campaign.name, "pct": round(pct, 1)}
