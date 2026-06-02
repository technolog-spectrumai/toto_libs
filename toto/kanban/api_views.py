import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.db import models as db_models

from toto.telegraph.api_views import CorsApiView
from toto.kanban.models import Project, Column, Task, Mission, Campaign, Practitioner, ProjectCommitment


def _project_to_dict(p):
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
    }


def _column_to_dict(c):
    return {"id": c.id, "name": c.name, "position": c.position, "can_add_task": c.can_add_task}


def _task_to_dict(t):
    return {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "weight": t.weight,
        "weight_label": t.weight_label,
        "column_id": t.column_id,
        "column_name": t.column.name if t.column else None,
        "mission_id": t.mission_id,
        "assignee": t.assignee.person.full_name if t.assignee and t.assignee.person else None,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "position": t.position,
    }


def _get_user_projects(user):
    """Return projects where the user is project lead or has an active practitioner commitment."""
    from toto.people.models import Person
    person = Person.objects.filter(user=user).first()
    if not person:
        return Project.objects.none()

    lead_ids = Project.objects.filter(project_lead=person).values_list("id", flat=True)
    committed_ids = (
        ProjectCommitment.objects.filter(
            practitioner__person=person,
            is_active=True,
        ).values_list("project_id", flat=True)
    )
    return Project.objects.filter(
        db_models.Q(id__in=lead_ids) | db_models.Q(id__in=committed_ids)
    ).distinct()


@method_decorator(csrf_exempt, name="dispatch")
class ProjectListApiView(CorsApiView):
    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        projects = _get_user_projects(request.user)
        return JsonResponse({"projects": [_project_to_dict(p) for p in projects]})


@method_decorator(csrf_exempt, name="dispatch")
class ProjectDetailApiView(CorsApiView):
    def get(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            project = Project.objects.get(pk=pk)
        except Project.DoesNotExist:
            return JsonResponse({"error": "Project not found."}, status=404)
        columns = list(Column.objects.filter(project=project).order_by("position"))
        data = _project_to_dict(project)
        data["columns"] = [_column_to_dict(c) for c in columns]
        return JsonResponse(data)


@method_decorator(csrf_exempt, name="dispatch")
class TaskListCreateApiView(CorsApiView):
    def get(self, request, project_pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        tasks = (
            Task.objects.filter(mission__campaign__project_id=project_pk)
            .select_related("column", "assignee__person", "mission")
            .order_by("column__position", "position")
        )
        return JsonResponse({"tasks": [_task_to_dict(t) for t in tasks]})

    def post(self, request, project_pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        title = data.get("title", "").strip()
        if not title:
            return JsonResponse({"error": "Title is required."}, status=400)

        column_id = data.get("column_id")
        if not column_id:
            return JsonResponse({"error": "column_id is required."}, status=400)

        try:
            column = Column.objects.get(pk=column_id, project_id=project_pk)
        except Column.DoesNotExist:
            return JsonResponse({"error": "Column not found in this project."}, status=404)

        # Find or create a default mission for tasks created via API
        project = column.project
        campaign = Campaign.objects.filter(project=project).first()
        if not campaign:
            campaign = Campaign.objects.create(
                project=project, name="Default Campaign", description=""
            )
        mission = Mission.objects.filter(campaign=campaign).first()
        if not mission:
            mission = Mission.objects.create(
                campaign=campaign, title="Default Mission", description=""
            )

        task = Task.objects.create(
            title=title,
            description=data.get("description", ""),
            column=column,
            mission=mission,
        )
        return JsonResponse(_task_to_dict(task), status=201)


@method_decorator(csrf_exempt, name="dispatch")
class TaskDetailApiView(CorsApiView):
    def _get_task(self, pk):
        try:
            return Task.objects.select_related("column__project", "assignee__person", "mission").get(pk=pk)
        except Task.DoesNotExist:
            return None

    def patch(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        task = self._get_task(pk)
        if not task:
            return JsonResponse({"error": "Task not found."}, status=404)
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        fields = []
        if "title" in data:
            task.title = data["title"]
            fields.append("title")
        if "description" in data:
            task.description = data["description"]
            fields.append("description")
        if "column_id" in data:
            try:
                task.column = Column.objects.get(pk=data["column_id"])
            except Column.DoesNotExist:
                return JsonResponse({"error": "Column not found."}, status=404)
            fields.append("column")

        if fields:
            task.save(update_fields=fields)
        return JsonResponse(_task_to_dict(task))


@method_decorator(csrf_exempt, name="dispatch")
class TaskPromoteApiView(CorsApiView):
    def post(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            task = Task.objects.select_related("column__project").get(pk=pk)
        except Task.DoesNotExist:
            return JsonResponse({"error": "Task not found."}, status=404)

        next_col = (
            Column.objects.filter(project=task.column.project, position__gt=task.column.position)
            .order_by("position")
            .first()
        )
        if not next_col:
            return JsonResponse({"error": "Already in the last column."}, status=400)

        task.column = next_col
        task.save(update_fields=["column"])
        return JsonResponse(_task_to_dict(task))


@method_decorator(csrf_exempt, name="dispatch")
class TaskDemoteApiView(CorsApiView):
    def post(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            task = Task.objects.select_related("column__project").get(pk=pk)
        except Task.DoesNotExist:
            return JsonResponse({"error": "Task not found."}, status=404)

        prev_col = (
            Column.objects.filter(project=task.column.project, position__lt=task.column.position)
            .order_by("-position")
            .first()
        )
        if not prev_col:
            return JsonResponse({"error": "Already in the first column."}, status=400)

        task.column = prev_col
        task.save(update_fields=["column"])
        return JsonResponse(_task_to_dict(task))
