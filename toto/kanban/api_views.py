import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.db import models as db_models

from toto.api.cors import CorsApiView, MeshGatedApiView
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
        "mission_title": t.mission.title if t.mission else None,
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
class ProjectListApiView(MeshGatedApiView):
    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        projects = _get_user_projects(request.user)
        return JsonResponse({"projects": [_project_to_dict(p) for p in projects]})


@method_decorator(csrf_exempt, name="dispatch")
class ProjectDetailApiView(MeshGatedApiView):
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
class TaskListCreateApiView(MeshGatedApiView):
    def get(self, request, project_pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        tasks = (
            Task.objects.filter(mission__campaign__project_id=project_pk)
            .select_related("column", "assignee__person", "mission", "mission__campaign")
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
        if "weight" in data:
            task.weight = int(data["weight"])
            fields.append("weight")
        if "due_date" in data:
            task.due_date = data["due_date"] or None
            fields.append("due_date")

        if fields:
            task.save(update_fields=fields)
        return JsonResponse(_task_to_dict(task))

    def delete(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        task = self._get_task(pk)
        if not task:
            return JsonResponse({"error": "Task not found."}, status=404)
        task.delete()
        return JsonResponse({}, status=204)


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


@method_decorator(csrf_exempt, name="dispatch")
class MissionDetailApiView(MeshGatedApiView):
    def get(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            mission = (
                Mission.objects.select_related(
                    "campaign__project__project_lead",
                    "campaign__owner",
                    "owner",
                    "location",
                    "route",
                    "campaign__zone",
                )
                .get(pk=pk)
            )
        except Mission.DoesNotExist:
            return JsonResponse({"error": "Mission not found."}, status=404)

        tasks = list(
            Task.objects.filter(mission=mission).select_related("column")
        )
        total = len(tasks)
        completed = sum(1 for t in tasks if t.completed_at is not None)
        open_count = total - completed
        total_weight = sum(t.weight for t in tasks)
        completed_weight = sum(t.weight for t in tasks if t.completed_at is not None)
        progress_pct = round((completed / total * 100), 1) if total else 0.0
        weighted_pct = round((completed_weight / total_weight * 100), 1) if total_weight else 0.0

        camp = mission.campaign
        proj = camp.project

        from toto.kanban.models import FIB_SCALE
        weight_labels = dict(FIB_SCALE)

        return JsonResponse({
            "id": mission.id,
            "title": mission.title,
            "description": mission.description,
            "urgency": mission.urgency,
            "urgency_label": mission.urgency_label,
            "impact": mission.impact,
            "impact_label": mission.impact_label,
            "campaign": {
                "id": camp.id,
                "name": camp.name,
                "description": camp.description,
                "start_date": camp.start_date.isoformat() if camp.start_date else None,
                "end_date": camp.end_date.isoformat() if camp.end_date else None,
                "owner": camp.owner.full_name if camp.owner else None,
            },
            "project": {
                "id": proj.id,
                "name": proj.name,
                "lead": proj.project_lead.full_name if proj.project_lead else None,
            },
            "owner": mission.owner.full_name if mission.owner else None,
            "location": str(mission.location) if mission.location else None,
            "route": str(mission.route) if mission.route else None,
            "zone": str(mission.campaign.zone) if mission.campaign.zone else None,
            "metadata": mission.metadata or {},
            "stats": {
                "total": total,
                "completed": completed,
                "open": open_count,
                "progress_pct": progress_pct,
                "total_weight": total_weight,
                "completed_weight": completed_weight,
                "weighted_pct": weighted_pct,
            },
            "tasks": [_task_to_dict(t) for t in tasks],
        })


@method_decorator(csrf_exempt, name="dispatch")
class ProjectMissionsApiView(MeshGatedApiView):
    def get(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        missions = (
            Mission.objects.filter(campaign__project_id=pk)
            .select_related("campaign")
            .order_by("campaign__name", "title")
        )
        return JsonResponse({
            "missions": [
                {
                    "id": m.id,
                    "title": m.title,
                    "campaign": m.campaign.name,
                    "urgency": m.urgency,
                    "urgency_label": m.urgency_label,
                    "impact": m.impact,
                    "impact_label": m.impact_label,
                }
                for m in missions
            ]
        })


@method_decorator(csrf_exempt, name="dispatch")
class SprintMetricsApiView(MeshGatedApiView):
    def get(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            project = Project.objects.get(pk=pk)
        except Project.DoesNotExist:
            return JsonResponse({"error": "Project not found."}, status=404)

        from toto.kanban.metrics import SprintMetricsCalculator
        calc = SprintMetricsCalculator(project)
        summary = calc.get_summary()

        def sprint_to_dict(item):
            return {
                "id": item["id"],
                "name": item["name"],
                "start": item["start"].isoformat() if item.get("start") else None,
                "end": item["end"].isoformat() if item.get("end") else None,
                "total_tasks": item["total_tasks"],
                "completed_tasks": item["completed_tasks"],
                "open_tasks": item["open_tasks"],
                "total_weight": item["total_weight"],
                "completed_weight": item["completed_weight"],
                "open_weight": item["open_weight"],
                "completion_rate": item["completion_rate"],
                "weight_completion_rate": item["weight_completion_rate"],
            }

        selected_id = request.GET.get("sprint")
        sprints = list(Sprint.objects.filter(project=project).order_by("-start_time"))
        selected = next((s for s in sprints if str(s.pk) == str(selected_id)), None) or (sprints[0] if sprints else None)

        burndown_labels = calc.get_burndown_labels(selected)
        burndown_data = calc.get_burndown_data(selected)
        velocity_labels = calc.get_velocity_labels()
        velocity_data = calc.get_velocity_data()
        lead_labels = calc.get_lead_labels()
        lead_data = calc.get_lead_data()
        assignee_items = calc.get_assignee_items()
        campaign_progress = calc.get_campaign_progress()

        return JsonResponse({
            "total_sprints": summary["total_sprints"],
            "total_tasks": summary["total_tasks"],
            "completed_tasks": summary["completed_tasks"],
            "open_tasks": summary["open_tasks"],
            "total_weight": summary["total_weight"],
            "completed_weight": summary["completed_weight"],
            "open_weight": summary["open_weight"],
            "overall_completion_rate": summary["overall_completion_rate"],
            "overall_weight_completion_rate": summary["overall_weight_completion_rate"],
            "sprint_items": [sprint_to_dict(s) for s in summary["sprint_items"]],
            "selected_sprint_id": selected.pk if selected else None,
            "burndown": {"labels": burndown_labels, "data": burndown_data},
            "velocity": {"labels": velocity_labels, "data": velocity_data},
            "lead_time": {"labels": lead_labels, "data": lead_data},
            "assignees": assignee_items,
            "campaign_progress": campaign_progress,
            "sprints": [{"id": s.pk, "name": s.name} for s in sprints],
        })


@method_decorator(csrf_exempt, name="dispatch")
class BacklogApiView(MeshGatedApiView):
    def get(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            project = Project.objects.get(pk=pk)
        except Project.DoesNotExist:
            return JsonResponse({"error": "Project not found."}, status=404)

        from toto.kanban.metrics import MissionMetricsCalculator
        calc = MissionMetricsCalculator(project)
        summary = calc.get_context_data()

        rows = []
        for item in summary["mission_items"]:
            m = item["mission"]
            rows.append({
                "id": m.id,
                "title": m.title,
                "campaign": item["campaign"],
                "urgency": m.urgency,
                "urgency_label": item["urgency"],
                "impact": m.impact,
                "impact_label": item["impact"],
                "total_tasks": item["total_tasks"],
                "completed_tasks": item["completed_tasks"],
                "open_tasks": item["open_tasks"],
                "total_weight": item["total_weight"],
                "completed_weight": item["completed_weight"],
                "completion_rate": item["completion_rate"],
                "weight_completion_rate": item["weight_completion_rate"],
                "tasks": [_task_to_dict(t) for t in item["tasks"]],
            })

        return JsonResponse({
            "total_missions": summary["total_missions"],
            "total_tasks": summary["total_tasks"],
            "completed_tasks": summary["completed_tasks"],
            "open_tasks": summary["open_tasks"],
            "overall_completion_rate": summary["overall_completion_rate"],
            "missions": rows,
        })


@method_decorator(csrf_exempt, name="dispatch")
class EisenhowerMatrixApiView(MeshGatedApiView):
    def get(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            project = Project.objects.get(pk=pk)
        except Project.DoesNotExist:
            return JsonResponse({"error": "Project not found."}, status=404)

        missions = (
            Mission.objects.filter(campaign__project=project)
            .select_related("campaign")
            .order_by("title")
        )

        matrix: dict = {}
        for u in (1, 2, 3):
            for i in (1, 2, 3):
                matrix[f"{u}_{i}"] = []

        for m in missions:
            key = f"{m.urgency}_{m.impact}"
            matrix[key].append({
                "id": m.id,
                "title": m.title,
                "campaign": m.campaign.name,
            })

        return JsonResponse({"matrix": matrix})
