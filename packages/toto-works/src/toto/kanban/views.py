import json

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
from toto.kanban.forms import TaskCreateForm
from toto.kanban.metrics import SprintMetricsCalculator, MissionMetricsCalculator
from toto.kanban.models import (
    Project, Column, Task, Sprint, Mission, DocumentationPage, Practitioner,
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

        columns = (
            Column.objects
            .filter(project=project)
            .order_by("position")
            .prefetch_related(
                "auditors",
                "tasks",
                "tasks__mission",
                "tasks__mission__campaign",
                "tasks__mission__campaign__zone",
                "tasks__mission__campaign__zone__territory",
                "tasks__mission__location",
                "tasks__mission__route",
                "tasks__assignee__person",
                "tasks__reviewer__person",
                "tasks__sprint",
                "tasks__column",
            )
        )

        for column in columns:
            column.is_auditor = column.auditors.filter(person__user=self.request.user).exists()

        context.update({
            "columns": columns,
            "sprints": sprints,
            "selected_sprint": selected_sprint,
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

        return (
            Project.objects
            .filter(
                Q(owner__user=user) |
                Q(practitioners__person__user=user, practitioners__is_active=True)
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
                "tasks__column",
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

        column_id = request.GET.get("column")

        if column_id:
            column = get_object_or_404(
                Column,
                pk=column_id,
                project=self.project,
            )

            if not column.can_add_task:
                return HttpResponseForbidden("You cannot add tasks to this column.")

        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.project
        return kwargs

    def form_valid(self, form):
        task = form.save(commit=False)

        column_id = self.request.GET.get("column")

        if column_id:
            task.column = get_object_or_404(
                Column,
                pk=column_id,
                project=self.project,
            )

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
        task = self.get_object()

        if not task.column.can_add_task:
            return HttpResponseForbidden("You cannot edit tasks in this column.")

        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            Task.objects
            .filter(mission__campaign__project_id=self.kwargs["project_pk"])
            .select_related("column", "mission", "mission__campaign")
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
        task = self.get_object()

        if not task.column.can_add_task:
            return HttpResponseForbidden("You cannot delete tasks in this column.")

        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            Task.objects
            .filter(mission__campaign__project_id=self.kwargs["project_pk"])
            .select_related("column", "mission", "mission__campaign")
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


def _get_adjacent_column(task, direction):
    current_position = task.column.position
    project = task.column.project

    if direction == "next":
        return (
            Column.objects
            .filter(
                project=project,
                position__gt=current_position,
            )
            .order_by("position")
            .first()
        )

    return (
        Column.objects
        .filter(
            project=project,
            position__lt=current_position,
        )
        .order_by("-position")
        .first()
    )


@login_required
def promote_task(request, project_id, task_id):
    project = get_object_or_404(Project, id=project_id)

    task = get_object_or_404(
        Task.objects.select_related("column", "mission", "mission__campaign", "reviewer__person__user"),
        id=task_id,
        mission__campaign__project=project,
    )

    next_column = _get_adjacent_column(task, "next")

    if not next_column:
        messages.warning(request, "Task is already in the last column.")
        return redirect("kanban:project_detail", pk=project_id)

    if not next_column.auditors.filter(person__user=request.user).exists():
        messages.error(
            request,
            "You are not allowed to promote tasks into this column.",
        )
        return redirect("kanban:project_detail", pk=project_id)

    is_terminal_column = not Column.objects.filter(
        project=project,
        position__gt=next_column.position,
    ).exists()
    reviewer_user_id = getattr(
        getattr(getattr(task.reviewer, "person", None), "user", None), "id", None
    )
    if is_terminal_column and reviewer_user_id and reviewer_user_id != request.user.id:
        messages.error(
            request,
            "This task has an assigned reviewer and only that reviewer can complete it.",
        )
        return redirect("kanban:project_detail", pk=project_id)

    task.column = next_column
    update_fields = ["column"]
    if is_terminal_column and not task.completed_at:
        task.completed_at = timezone.now()
        update_fields.append("completed_at")
    task.save(update_fields=update_fields)
    messages.success(request, f"Task promoted to {next_column.name}.")
    return redirect("kanban:project_detail", pk=project_id)


@login_required
def demote_task(request, project_id, task_id):
    project = get_object_or_404(Project, id=project_id)

    task = get_object_or_404(
        Task.objects.select_related("column", "mission", "mission__campaign"),
        id=task_id,
        mission__campaign__project=project,
    )

    previous_column = _get_adjacent_column(task, "prev")

    if not previous_column:
        messages.warning(request, "Task is already in the first column.")
        return redirect("kanban:project_detail", pk=project_id)

    if not previous_column.auditors.filter(person__user=request.user).exists():
        messages.error(
            request,
            "You are not allowed to demote tasks into this column.",
        )
        return redirect("kanban:project_detail", pk=project_id)

    task.column = previous_column
    update_fields = ["column"]
    if task.completed_at:
        task.completed_at = None
        update_fields.append("completed_at")
    task.save(update_fields=update_fields)

    messages.success(request, f"Task moved back to {previous_column.name}.")
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
        calculator = SprintMetricsCalculator(project)
        metrics = calculator.get_context_data()

        sprints = (
            Sprint.objects
            .filter(project=project)
            .order_by("-start_time")
        )

        selected_sprint = self.get_selected_sprint(project)

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

        burndown_chart = self.line_chart(
            labels=calculator.get_burndown_labels(selected_sprint),
            datasets=[{
                "label": "Remaining Weight",
                "data": calculator.get_burndown_data(selected_sprint),
                "borderColor": self.accent_color,
                "backgroundColor": self.accent_color,
                "tension": 0.35,
            }],
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
                "label": "Lead Time Days",
                "data": metrics["lead_data"],
                "backgroundColor": self.accent_alt_color,
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
        })

        return context


class MissionMetricsView(LoginRequiredMixin, ChartViewMixin, DetailView):
    model = Project
    template_name = "kanban/mission_metrics.html"
    context_object_name = "project"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)

        project = self.object
        calculator = MissionMetricsCalculator(project)
        metrics = calculator.get_context_data()

        mission_items = metrics["mission_items"]

        mission_task_chart = self.bar_chart(
            labels=[item["title"] for item in mission_items],
            datasets=[
                {
                    "label": "Completed",
                    "data": [item["completed_tasks"] for item in mission_items],
                    "backgroundColor": self.success_color,
                },
                {
                    "label": "Open",
                    "data": [item["open_tasks"] for item in mission_items],
                    "backgroundColor": self.accent_color,
                },
            ],
            stacked=True,
        )

        mission_completion_chart = self.bar_chart(
            labels=[item["title"] for item in mission_items],
            datasets=[{
                "label": "Completion %",
                "data": [item["completion_rate"] for item in mission_items],
                "backgroundColor": self.chart_colors,
            }],
            max_y=100,
        )

        mission_weight_chart = self.bar_chart(
            labels=[item["title"] for item in mission_items],
            datasets=[{
                "label": "Total Weight",
                "data": [item["total_weight"] for item in mission_items],
                "backgroundColor": self.accent_alt_color,
            }],
        )

        context.update({
            **metrics,
            "mission_task_chart_json": self.chart_json(mission_task_chart),
            "mission_completion_chart_json": self.chart_json(mission_completion_chart),
            "mission_weight_chart_json": self.chart_json(mission_weight_chart),
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
                "tasks__column",
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
        tasks = mission.tasks.all()

        total_tasks = tasks.count()
        completed_tasks = tasks.filter(completed_at__isnull=False).count()
        open_tasks = total_tasks - completed_tasks

        total_weight = tasks.aggregate(total=Sum("weight"))["total"] or 0

        completed_weight = (
            tasks
            .filter(completed_at__isnull=False)
            .aggregate(total=Sum("weight"))["total"]
            or 0
        )

        completion_rate = (
            round((completed_tasks / total_tasks) * 100, 1)
            if total_tasks
            else 0
        )

        weight_completion_rate = (
            round((completed_weight / total_weight) * 100, 1)
            if total_weight
            else 0
        )

        try:
            documentation_page = mission.documentation_page
        except mission.__class__.documentation_page.RelatedObjectDoesNotExist:
            documentation_page = None

        context.update({
            "project": project,
            "tasks": tasks,
            "documentation_page": documentation_page,

            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "open_tasks": open_tasks,

            "total_weight": total_weight,
            "completed_weight": completed_weight,

            "completion_rate": completion_rate,
            "weight_completion_rate": weight_completion_rate,
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
