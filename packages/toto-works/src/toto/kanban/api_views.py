import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.db import models as db_models

from toto.api.cors import CorsApiView, MeshGatedApiView
from toto.kanban.models import (
    Project, Task, Mission, Campaign, Practitioner, ProjectCommitment, Sprint,
    TaskStatus, STATUS_ORDER, adjacent_status, FIB_SCALE,
    visible_missions_for, visible_tasks_for,
)
from toto.kanban.views import is_project_auditor


def _project_to_dict(p):
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
    }


def _status_to_dict(value):
    return {"status": value, "label": str(TaskStatus(value).label), "position": STATUS_ORDER.index(value)}


def _task_to_dict(t):
    return {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "weight": t.weight,
        "weight_label": t.weight_label,
        "status": t.status,
        "status_label": str(t.get_status_display()),
        # Kept one release for the out-of-tree Enigma client, which reads this
        # for display. Writes naming column_id are refused, not ignored.
        "column_name": str(t.get_status_display()),
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
        data = _project_to_dict(project)
        data["columns"] = [_status_to_dict(value) for value in STATUS_ORDER]
        return JsonResponse(data)


@method_decorator(csrf_exempt, name="dispatch")
class TaskListCreateApiView(MeshGatedApiView):
    def get(self, request, project_pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        tasks = (
            visible_tasks_for(
                request.user,
                Task.objects.filter(mission__campaign__project_id=project_pk),
            )
            .select_related("assignee__person", "mission", "mission__campaign")
            .in_board_order()
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

        if "column_id" in data:
            return JsonResponse(
                {"error": "column_id is gone; tasks are created in 'todo'. Use PATCH status to move one."},
                status=400,
            )

        try:
            project = Project.objects.get(pk=project_pk)
        except Project.DoesNotExist:
            return JsonResponse({"error": "Project not found."}, status=404)

        # Find or create a default mission for tasks created via API
        campaign = Campaign.objects.filter(project=project).first()
        if not campaign:
            campaign = Campaign.objects.create(
                project=project, name="Default Campaign", description=""
            )
        mission = visible_missions_for(
            request.user, Mission.objects.filter(campaign=campaign)
        ).first()
        if not mission:
            mission = Mission.objects.create(
                campaign=campaign, title="Default Mission", description=""
            )

        task = Task.objects.create(
            title=title,
            description=data.get("description", ""),
            status=TaskStatus.TODO,
            mission=mission,
        )
        return JsonResponse(_task_to_dict(task), status=201)


@method_decorator(csrf_exempt, name="dispatch")
class TaskDetailApiView(CorsApiView):
    def _get_task(self, pk, user):
        try:
            return visible_tasks_for(
                user,
                Task.objects.select_related(
                    "assignee__person", "mission", "mission__campaign__project",
                ),
            ).get(pk=pk)
        except Task.DoesNotExist:
            return None

    def patch(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        task = self._get_task(pk, request.user)
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
            return JsonResponse(
                {"error": "column_id is gone. Send status: todo | in_progress | done."},
                status=400,
            )
        if "status" in data:
            if data["status"] not in TaskStatus.values:
                return JsonResponse(
                    {"error": f"status must be one of {', '.join(TaskStatus.values)}."},
                    status=400,
                )
            if not is_project_auditor(request.user, task.mission.campaign.project):
                return JsonResponse({"error": "You cannot move tasks in this project."}, status=403)
            task.status = data["status"]
            fields.append("status")
        if "weight" in data:
            # int("abc") used to raise straight out of here as a 500, and a
            # negative weight was accepted and poisoned every weighted metric.
            try:
                weight = int(data["weight"])
            except (TypeError, ValueError):
                return JsonResponse({"error": "weight must be an integer."}, status=400)
            if weight not in dict(FIB_SCALE):
                return JsonResponse(
                    {"error": f"weight must be one of {sorted(dict(FIB_SCALE))}."},
                    status=400,
                )
            task.weight = weight
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
        task = self._get_task(pk, request.user)
        if not task:
            return JsonResponse({"error": "Task not found."}, status=404)
        task.delete()
        return JsonResponse({}, status=204)


def _load_movable_task(request, pk):
    """Fetch a task for a move, or return the error response instead.

    These endpoints carried no membership, auditor or reviewer check at all —
    any authenticated mesh user could move any task by primary key. They now
    apply the same gates as the board.
    """
    if not request.user or not request.user.is_authenticated:
        return None, JsonResponse({"error": "Not authenticated."}, status=401)

    try:
        task = visible_tasks_for(
            request.user,
            Task.objects.select_related(
                "mission__campaign__project", "reviewer__person__user",
            ),
        ).get(pk=pk)
    except Task.DoesNotExist:
        return None, JsonResponse({"error": "Task not found."}, status=404)

    if not is_project_auditor(request.user, task.mission.campaign.project):
        return None, JsonResponse({"error": "You cannot move tasks in this project."}, status=403)

    return task, None


@method_decorator(csrf_exempt, name="dispatch")
class TaskPromoteApiView(CorsApiView):
    def post(self, request, pk):
        task, error = _load_movable_task(request, pk)
        if error:
            return error

        next_status = adjacent_status(task.status, "next")
        if next_status is None:
            return JsonResponse({"error": "Already done."}, status=400)

        if next_status == TaskStatus.DONE:
            reviewer_user_id = getattr(
                getattr(getattr(task.reviewer, "person", None), "user", None), "id", None
            )
            if reviewer_user_id and reviewer_user_id != request.user.id:
                return JsonResponse(
                    {"error": "Only the assigned reviewer can complete this task."},
                    status=403,
                )

        blockers = list(task.open_blockers().values_list("title", flat=True)[:5])

        task.status = next_status
        task.save(update_fields=["status"])  # completed_at follows, in Task.save

        payload = _task_to_dict(task)
        payload["open_blockers"] = blockers
        return JsonResponse(payload)


@method_decorator(csrf_exempt, name="dispatch")
class TaskDemoteApiView(CorsApiView):
    def post(self, request, pk):
        task, error = _load_movable_task(request, pk)
        if error:
            return error

        previous_status = adjacent_status(task.status, "prev")
        if previous_status is None:
            return JsonResponse({"error": "Already at the start of the board."}, status=400)

        task.status = previous_status
        task.save(update_fields=["status"])
        return JsonResponse(_task_to_dict(task))


@method_decorator(csrf_exempt, name="dispatch")
class MissionDetailApiView(MeshGatedApiView):
    def get(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            mission = (
                visible_missions_for(request.user, Mission.objects.all()).select_related(
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
            Task.objects.filter(mission=mission)
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
            "zone": str(mission.effective_zone) if mission.effective_zone else None,
            "visibility": mission.visibility,
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
            visible_missions_for(request.user, Mission.objects.filter(campaign__project_id=pk))
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
        calc = SprintMetricsCalculator(
            project, sprint_id=request.GET.get("sprint"), user=request.user
        )
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

        sprints = calc.sprints
        selected = calc.selected_sprint

        burndown = calc.get_burndown(selected)
        velocity = calc.get_velocity()
        days_to_completion = calc.get_days_to_completion()
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
            "burndown": {
                **burndown,
                # "data" is what the out-of-tree client reads; kept as an alias
                # of the actual series for one release.
                "data": burndown["actual"],
            },
            "status_counts": calc.get_status_counts(),
            "velocity": velocity,
            "lead_time": days_to_completion,
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
        calc = MissionMetricsCalculator(project, user=request.user)
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
            visible_missions_for(request.user, Mission.objects.filter(campaign__project=project))
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
