# kanban/client.py
from datetime import date
from .models import Project, Sprint, Task, Campaign, Mission, Column


class KanbanClient:

    @staticmethod
    def format_date(d: date, fmt: str = "%b %d") -> str:
        """
        Format a date object into a string using the given format.
        Default format is abbreviated month + day (e.g. 'Dec 14').
        """
        return d.strftime(fmt)

    # --- Queries returning dicts ---
    def get_project(self, project_id: int) -> dict:
        project = Project.objects.filter(id=project_id).first()
        if not project:
            return {}
        return self._project_to_dict(project)

    def get_projects(self) -> list[dict]:
        return [self._project_to_dict(p) for p in Project.objects.all()]

    def get_sprints(self, project_id: int) -> list[dict]:
        project = Project.objects.filter(id=project_id).first()
        if not project:
            return []
        return [self._sprint_to_dict(sp) for sp in Sprint.objects.filter(project=project).order_by("start_time")]

    def latest_sprint(self, project_id: int) -> dict:
        project = Project.objects.filter(id=project_id).first()
        if not project:
            return {}
        sprint = Sprint.objects.filter(project=project).order_by("-start_time").first()
        return self._sprint_to_dict(sprint) if sprint else {}

    def get_tasks(self, project_id: int) -> list[dict]:
        project = Project.objects.filter(id=project_id).first()
        if not project:
            return []
        return [self._task_to_dict(t) for t in Task.objects.filter(mission__campaign__project=project)]

    def get_campaigns(self, project_id: int) -> list[dict]:
        project = Project.objects.filter(id=project_id).first()
        if not project:
            return []
        return [self._campaign_to_dict(c) for c in Campaign.objects.filter(project=project)]

    def get_columns(self, project_id: int) -> list[dict]:
        project = Project.objects.filter(id=project_id).first()
        if not project:
            return []
        return [self._column_to_dict(c) for c in Column.objects.filter(project=project).order_by("position")]

    # --- Protected dict converters ---
    def _project_to_dict(self, project: Project) -> dict:
        return {"id": project.id, "name": project.name, "slug": project.slug}

    def _sprint_to_dict(self, sprint: Sprint) -> dict:
        return {
            "id": sprint.id,
            "name": sprint.name,
            "start_date": sprint.start_time.date(),
            "end_date": sprint.end_time.date(),
            "tasks": [self._task_to_dict(t) for t in sprint.tasks.all()],
        }

    def _task_to_dict(self, task: Task) -> dict:
        return {
            "id": task.id,
            "title": task.title,
            "weight": task.weight,
            "assignee": getattr(task.assignee, "username", None),
            "completed_at": task.completed_at.date() if task.completed_at else None,
            "sprint_start": task.sprint.start_time.date() if task.sprint else None,
        }

    def _campaign_to_dict(self, campaign: Campaign) -> dict:
        return {
            "id": campaign.id,
            "name": campaign.name,
            "missions": [
                {
                    "id": m.id,
                    "title": m.title,
                    "tasks": [self._task_to_dict(t) for t in m.tasks.all()],
                }
                for m in campaign.missions.all()
            ],
        }

    def _column_to_dict(self, column: Column) -> dict:
        return {"id": column.id, "name": column.name, "position": column.position}
