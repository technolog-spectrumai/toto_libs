import json
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.views.generic import DetailView, ListView, UpdateView, CreateView, DeleteView
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor
from toto.kanban.forms import TaskCreateForm, TaskRelationForm
from toto.kanban.metrics import (
    SprintMetricsCalculator, MissionMetricsCalculator, summarize_tasks,
)
from toto.kanban.models import (
    Project, Task, TaskRelation, TaskStatus, RelationType, STATUS_ORDER,
    adjacent_status, Sprint, Mission, DocumentationPage, Practitioner,
)
from toto.kanban.plugins.mission_plugins import MissionPlugin
from toto.kanban.plugins.mission_tab_plugins import MissionTabPlugin
from toto.verbena.views import PageDetailMixin


class ChartViewMixin:
    chart_colors = [
        "#4f5fa1",
        "#2f3d63",
        "#5fa38c",
        "#4a8f7a",
        "#ff4455",
        "#d94a4a",
    ]

    success_color = "#5fa38c"
    accent_color = "#4f5fa1"
    accent_alt_color = "#2f3d63"
    warn_color = "#ff4455"

    def chart_json(self, chart):
        return json.dumps(chart)

    def bar_chart(self, labels, datasets, stacked=False, max_y=None):
        y_options = {
            "beginAtZero": True,
        }

        if max_y is not None:
            y_options["max"] = max_y

        return {
            "chart_type": "bar",
            "labels": labels,
            "datasets": datasets,
            "options": {
                "scales": {
                    "x": {
                        "stacked": stacked,
                    },
                    "y": {
                        **y_options,
                        "stacked": stacked,
                    },
                }
            },
        }

    def line_chart(self, labels, datasets):
        return {
            "chart_type": "line",
            "labels": labels,
            "datasets": datasets,
            "options": {
                "scales": {
                    "y": {
                        "beginAtZero": True,
                    }
                }
            },
        }


def is_project_auditor(user, project):
    """May this user move tasks between states?

    Replaces the per-column ``Column.auditors`` gate: with three fixed states
    there is no row to hang a per-state grant on, so the grant is per project.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    return project.auditors.filter(person__user=user).exists()


def can_manage_tasks(user, project):
    """May this user create, edit or delete tasks in this project?

    Replaces ``Column.can_add_task``, which gated create *and* edit *and* delete
    per column — under fixed states that would have meant only ``todo`` tasks
    were ever editable. Project membership is the honest reading of what that
    flag was reaching for.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_staff or user.is_superuser:
        return True
    if is_project_auditor(user, project):
        return True
    return Practitioner.objects.filter(
        person__user=user,
        is_active=True,
        commitments__project=project,
        commitments__is_active=True,
    ).exists()


class ProjectDetailView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = "kanban/board.html"
    context_object_name = "project"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)

        project = self.get_object()
        sprint_id = self.request.GET.get("sprint")
        sprints = Sprint.objects.filter(project=project).order_by("-start_time")

        selected_sprint = None
        if sprint_id:
            try:
                selected_sprint = sprints.get(id=sprint_id)
            except Sprint.DoesNotExist:
                selected_sprint = None

        tasks = (
            Task.objects
            .filter(mission__campaign__project=project)
            .select_related(
                "mission",
                "mission__campaign",
                "mission__campaign__zone",
                "mission__campaign__zone__territory",
                "mission__location",
                "mission__route",
                "assignee__person",
                "reviewer__person",
                "sprint",
            )
            .prefetch_related(
                "incoming_relations__from_task",
                "outgoing_relations__to_task",
            )
            .order_by("position", "pk")
        )

        # Group in Python: the states are fixed, so one pass beats a query per
        # column, and every state renders even when it holds nothing.
        tasks = list(tasks)
        grouped = {value: [] for value in STATUS_ORDER}
        by_campaign = defaultdict(list)
        for task in tasks:
            grouped.setdefault(task.status, []).append(task)
            by_campaign[task.mission.campaign_id].append(task)

        # Both of these come off rows already fetched. Asking the model for them
        # per card — task.open_blockers, or a sibling queryset — would be a query
        # per card, which is what the prefetch above exists to avoid.
        for task in tasks:
            task.campaign_siblings = [
                sibling
                for sibling in by_campaign[task.mission.campaign_id]
                if sibling.pk != task.pk
            ]
            task.blockers = [
                relation.from_task
                for relation in task.incoming_relations.all()
                if relation.relation_type == RelationType.BLOCKS
                and relation.from_task.status != TaskStatus.DONE
            ]

        is_auditor = is_project_auditor(self.request.user, project)
        columns = [
            {
                "status": value,
                "label": TaskStatus(value).label,
                "tasks": grouped.get(value, []),
                "count": len(grouped.get(value, [])),
                "is_auditor": is_auditor,
            }
            for value in STATUS_ORDER
        ]

        context.update({
            "columns": columns,
            "sprints": sprints,
            "selected_sprint": selected_sprint,
            "is_auditor": is_auditor,
            "can_manage": can_manage_tasks(self.request.user, project),
            "relation_type_choices": RelationType.choices,
        })

        return context


class ProjectListView(LoginRequiredMixin, ListView):
    model = Project
    template_name = "kanban/project_list.html"
    context_object_name = "projects"

    def get_queryset(self):
        user = self.request.user

        if isinstance(user, AnonymousUser) or not user.is_authenticated:
            return Project.objects.none()

        if user.is_superuser or user.is_staff:
            return Project.objects.all().distinct()

        # Was Q(owner__user=…) | Q(practitioners__…), naming two relations
        # Project does not have — so this raised FieldError for every non-staff
        # user who reached it. The lead is `project_lead`, and membership runs
        # through ProjectCommitment.
        return (
            Project.objects
            .filter(
                Q(project_lead__user=user)
                | Q(commitments__practitioner__person__user=user, commitments__is_active=True)
                | Q(auditors__person__user=user)
            )
            .distinct()
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


class EisenhowerMatrixView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = "kanban/eisenhower.html"
    context_object_name = "project"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)

        project = self.get_object()

        missions = (
            Mission.objects
            .filter(campaign__project=project)
            .select_related("campaign")
        )

        urgency_levels = {
            1: "Low",
            2: "Medium",
            3: "High",
        }

        impact_levels = {
            1: "Low",
            2: "Medium",
            3: "High",
        }

        matrix = []

        for urgency_value, urgency_label in urgency_levels.items():
            row = []

            for impact_value, impact_label in impact_levels.items():
                row.append({
                    "urgency": urgency_label,
                    "impact": impact_label,
                    "impact_label": impact_label,
                    "missions": missions.filter(
                        urgency=urgency_value,
                        impact=impact_value,
                    ),
                })

            matrix.append({
                "urgency": urgency_label,
                "cells": row,
            })

        context.update({
            "matrix": matrix,
            "impact_levels": impact_levels.values(),
        })

        return context


class BacklogView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = "kanban/backlog.html"
    context_object_name = "project"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)

        project = self.get_object()

        missions = (
            Mission.objects
            .filter(campaign__project=project)
            .select_related(
                "campaign",
                "campaign__zone",
                "campaign__zone__territory",
                "location",
                "route",
                "owner",
            )
            .prefetch_related(
                "tasks",
                "tasks__sprint",
                "tasks__assignee__person",
            )
        )

        context.update({
            "missions": missions,
        })

        return context


class TaskCreateView(LoginRequiredMixin, CreateView):
    model = Task
    form_class = TaskCreateForm
    template_name = "kanban/create_task.html"
    context_object_name = "task"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(Project, pk=kwargs["pk"])

        if request.user.is_authenticated and not can_manage_tasks(request.user, self.project):
            return HttpResponseForbidden("You cannot add tasks to this project.")

        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.project
        return kwargs

    def form_valid(self, form):
        task = form.save(commit=False)
        # Work always starts at the left of the board. There is nothing to pick
        # and nothing to gate.
        task.status = TaskStatus.TODO
        task.save()

        if hasattr(form, "save_m2m"):
            form.save_m2m()

        return super().form_valid(form)

    def get_success_url(self):
        return reverse(
            "kanban:project_detail",
            kwargs={"pk": self.project.pk},
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["project"] = self.project
        return PageProcessor().decorate(context, self.request)


class TaskUpdateView(LoginRequiredMixin, UpdateView):
    model = Task
    form_class = TaskCreateForm
    template_name = "kanban/edit_task.html"
    context_object_name = "task"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(Project, pk=kwargs["project_pk"])

        if request.user.is_authenticated and not can_manage_tasks(request.user, self.project):
            return HttpResponseForbidden("You cannot edit tasks in this project.")

        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            Task.objects
            .filter(mission__campaign__project_id=self.kwargs["project_pk"])
            .select_related("mission", "mission__campaign")
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.project
        return kwargs

    def get_success_url(self):
        return reverse(
            "kanban:project_detail",
            kwargs={"pk": self.project.pk},
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["project"] = self.project
        return PageProcessor().decorate(context, self.request)


class TaskDeleteView(LoginRequiredMixin, DeleteView):
    model = Task
    template_name = "kanban/delete_task.html"
    context_object_name = "task"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(Project, pk=kwargs["project_pk"])

        if request.user.is_authenticated and not can_manage_tasks(request.user, self.project):
            return HttpResponseForbidden("You cannot delete tasks in this project.")

        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            Task.objects
            .filter(mission__campaign__project_id=self.kwargs["project_pk"])
            .select_related("mission", "mission__campaign")
        )

    def get_success_url(self):
        return reverse(
            "kanban:project_detail",
            kwargs={"pk": self.project.pk},
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["project"] = self.project
        return PageProcessor().decorate(context, self.request)


@login_required
@require_POST
def promote_task(request, project_id, task_id):
    project = get_object_or_404(Project, id=project_id)

    task = get_object_or_404(
        Task.objects.select_related("mission", "mission__campaign", "reviewer__person__user"),
        id=task_id,
        mission__campaign__project=project,
    )

    next_status = adjacent_status(task.status, "next")

    if next_status is None:
        messages.warning(request, "Task is already done.")
        return redirect("kanban:project_detail", pk=project_id)

    if not is_project_auditor(request.user, project):
        messages.error(request, "You are not allowed to move tasks in this project.")
        return redirect("kanban:project_detail", pk=project_id)

    # Pinned to `done` rather than "the last column by position", which used to
    # follow any column an admin added after Done.
    if next_status == TaskStatus.DONE:
        reviewer_user_id = getattr(
            getattr(getattr(task.reviewer, "person", None), "user", None), "id", None
        )
        if reviewer_user_id and reviewer_user_id != request.user.id:
            messages.error(
                request,
                "This task has an assigned reviewer and only that reviewer can complete it.",
            )
            return redirect("kanban:project_detail", pk=project_id)

    # Blockers advise, they do not refuse: a board that blocks the move gets
    # worked around by deleting the relation, which loses the information.
    blockers = list(task.open_blockers().values_list("title", flat=True)[:5])

    task.status = next_status
    task.save(update_fields=["status"])  # completed_at follows, in Task.save

    if blockers:
        messages.warning(
            request,
            "Moved, but {count} blocker(s) are still open: {titles}.".format(
                count=len(blockers), titles=", ".join(blockers)
            ),
        )
    messages.success(request, f"Task promoted to {task.get_status_display()}.")
    return redirect("kanban:project_detail", pk=project_id)


@login_required
@require_POST
def demote_task(request, project_id, task_id):
    project = get_object_or_404(Project, id=project_id)

    task = get_object_or_404(
        Task.objects.select_related("mission", "mission__campaign"),
        id=task_id,
        mission__campaign__project=project,
    )

    previous_status = adjacent_status(task.status, "prev")

    if previous_status is None:
        messages.warning(request, "Task is already at the start of the board.")
        return redirect("kanban:project_detail", pk=project_id)

    if not is_project_auditor(request.user, project):
        messages.error(request, "You are not allowed to move tasks in this project.")
        return redirect("kanban:project_detail", pk=project_id)

    # No reviewer gate on the way back: gating it would strand finished tasks
    # that nobody but an absent reviewer could reopen.
    task.status = previous_status
    task.save(update_fields=["status"])

    messages.success(request, f"Task moved back to {task.get_status_display()}.")
    return redirect("kanban:project_detail", pk=project_id)


@login_required
@require_POST
def relation_create(request, project_id, task_id):
    """Link this task to another in the same campaign."""
    project = get_object_or_404(Project, id=project_id)
    task = get_object_or_404(
        Task.objects.select_related("mission"),
        id=task_id,
        mission__campaign__project=project,
    )

    if not can_manage_tasks(request.user, project):
        return HttpResponseForbidden("You cannot edit tasks in this project.")

    form = TaskRelationForm(request.POST, from_task=task)
    if form.is_valid():
        form.save()
        messages.success(request, "Relation added.")
    else:
        for error in form.errors.values():
            messages.error(request, "; ".join(error))

    return redirect("kanban:project_detail", pk=project_id)


@login_required
@require_POST
def relation_delete(request, project_id, pk):
    project = get_object_or_404(Project, id=project_id)

    if not can_manage_tasks(request.user, project):
        return HttpResponseForbidden("You cannot edit tasks in this project.")

    relation = get_object_or_404(
        TaskRelation,
        pk=pk,
        from_task__mission__campaign__project=project,
    )
    relation.delete()
    messages.success(request, "Relation removed.")
    return redirect("kanban:project_detail", pk=project_id)


class SprintMetricsView(LoginRequiredMixin, ChartViewMixin, DetailView):
    model = Project
    template_name = "kanban/sprint_metrics.html"
    context_object_name = "project"

    def get_selected_sprint(self, project):
        sprint_id = self.request.GET.get("sprint")

        sprints = (
            Sprint.objects
            .filter(project=project)
            .order_by("-start_time")
        )

        if sprint_id:
            try:
                return sprints.get(pk=sprint_id)
            except Sprint.DoesNotExist:
                return sprints.first()

        return sprints.first()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)

        project = self.object
        calculator = SprintMetricsCalculator(project, sprint_id=self.request.GET.get("sprint"))
        selected_sprint = calculator.selected_sprint
        metrics = calculator.get_context_data()

        sprints = calculator.sprints
        sprint_items = metrics["sprint_items"]
        assignee_items = metrics["assignee_items"]

        sprint_completion_chart = self.bar_chart(
            labels=[item["name"] for item in sprint_items],
            datasets=[{
                "label": "Completion %",
                "data": [item["completion_rate"] for item in sprint_items],
                "backgroundColor": self.chart_colors,
            }],
            max_y=100,
        )

        sprint_task_chart = self.bar_chart(
            labels=[item["name"] for item in sprint_items],
            datasets=[
                {
                    "label": "Completed",
                    "data": [item["completed_tasks"] for item in sprint_items],
                    "backgroundColor": self.success_color,
                },
                {
                    "label": "Open",
                    "data": [item["open_tasks"] for item in sprint_items],
                    "backgroundColor": self.accent_color,
                },
            ],
            stacked=True,
        )

        burndown = metrics["burndown"]
        burndown_chart = self.line_chart(
            labels=burndown["labels"],
            datasets=[
                {
                    "label": "Remaining weight",
                    "data": burndown["actual"],
                    "borderColor": self.accent_color,
                    "backgroundColor": self.accent_color,
                    "tension": 0.35,
                    # Explicit, though Chart.js already defaults to false: the
                    # nulls after today are the point, and bridging them would
                    # redraw the flat tail this replaced.
                    "spanGaps": False,
                },
                {
                    "label": "Ideal",
                    "data": burndown["ideal"],
                    "borderColor": self.accent_alt_color,
                    "backgroundColor": "transparent",
                    "borderDash": [6, 4],
                    "borderWidth": 1,
                    "pointRadius": 0,
                    "tension": 0,
                },
            ],
        )

        velocity_chart = self.bar_chart(
            labels=metrics["velocity_labels"],
            datasets=[{
                "label": "Completed Weight",
                "data": metrics["velocity_data"],
                "backgroundColor": self.success_color,
            }],
        )

        lead_time_chart = self.bar_chart(
            labels=metrics["lead_labels"],
            datasets=[{
                "label": "Days from sprint start",
                "data": metrics["lead_data"],
                "backgroundColor": self.accent_alt_color,
            }],
        )

        status_counts = metrics["status_counts"]
        status_chart = self.bar_chart(
            labels=[item["label"] for item in status_counts],
            datasets=[{
                "label": "Tasks",
                "data": [item["tasks"] for item in status_counts],
                "backgroundColor": self.chart_colors,
            }],
        )

        assignee_workload_chart = self.bar_chart(
            labels=[item["label"] for item in assignee_items],
            datasets=[
                {
                    "label": "Completed Weight",
                    "data": [item["completed_weight"] for item in assignee_items],
                    "backgroundColor": self.success_color,
                },
                {
                    "label": "Open Weight",
                    "data": [item["open_weight"] for item in assignee_items],
                    "backgroundColor": self.accent_color,
                },
            ],
            stacked=True,
        )

        context.update({
            **metrics,

            # Historic sprint selector context
            "sprints": sprints,
            "selected_sprint": selected_sprint,

            # Keep this for old template compatibility if needed
            "latest_sprint": selected_sprint,

            "sprint_completion_chart_json": self.chart_json(sprint_completion_chart),
            "sprint_task_chart_json": self.chart_json(sprint_task_chart),

            "burndown_chart_json": self.chart_json(burndown_chart),
            "velocity_chart_json": self.chart_json(velocity_chart),
            "lead_time_chart_json": self.chart_json(lead_time_chart),
            "assignee_workload_chart_json": self.chart_json(assignee_workload_chart),
            "status_chart_json": self.chart_json(status_chart),
        })

        return context


class MissionDetailView(LoginRequiredMixin, DetailView):
    model = Mission
    template_name = "kanban/mission_detail.html"
    context_object_name = "mission"

    def dispatch(self, request, *args, **kwargs):
        # Missions are gated to the data mesh: non-members get the nice access-denied page
        # and must pull this data from a peer (see toto.api.cors).
        from toto.api.cors import in_data_mesh, render_access_denied
        if request.user.is_authenticated and not in_data_mesh(request.user):
            return render_access_denied(request)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            Mission.objects
            .select_related(
                "campaign",
                "campaign__project",
                "campaign__owner",
                "owner",
                "campaign__zone",
                "campaign__zone__territory",
                "documentation_page",
            )
            .prefetch_related(
                "tasks",
                "tasks__sprint",
                "tasks__assignee__person",
                "tasks__reviewer__person",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)

        mission = self.object
        project = mission.campaign.project
        # get_queryset already prefetched these. Filtering the manager here
        # would throw that away and re-query for numbers already in memory.
        tasks = list(mission.tasks.all())

        try:
            documentation_page = mission.documentation_page
        except mission.__class__.documentation_page.RelatedObjectDoesNotExist:
            documentation_page = None

        context.update({
            "project": project,
            "tasks": tasks,
            "documentation_page": documentation_page,
            **summarize_tasks(tasks),
        })
        context["mission_plugin_sections"] = MissionPlugin.render_all(
            request=self.request,
            mission=mission,
            project=project,
            tasks=tasks,
            base_context=context,
        )
        context["mission_tabs"] = [
            {"url": tab.get_tab_url(mission), "title": tab.get_title(), "icon": tab.tab_icon}
            for tab in MissionTabPlugin.visible(request=self.request, mission=mission)
        ]

        return context

class DocumentationPageDetailView(PageDetailMixin, DetailView):
    model = DocumentationPage
    template_name = "kanban/documentation_page_detail.html"
    context_object_name = "page"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["sections"] = self.render_sections(self.object)
        context["back_url"] = "kanban:mission_detail"
        context["back_url_pk"] = self.object.mission.pk
        context["back_label"] = self.object.mission.title
        context["page_type_label"] = "Documentation"
        return PageProcessor().decorate(context, self.request)


# mission_economy, project_tokenize, and project_tokenization_default
# have moved to toto.mission_economy.views.
