from django.http import HttpResponseForbidden
from django.views.generic import DetailView, ListView, UpdateView
from django.contrib.auth.models import AnonymousUser
from toto.kanban.models import Project, Column, Task, Sprint, Mission
from django.contrib.auth.mixins import LoginRequiredMixin
from toto.core.page import PageProcessor
from django.urls import reverse
from django.views.generic import CreateView
from toto.kanban.forms import TaskCreateForm
from django.views.generic import DeleteView
from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
import json
from django.db.models import Count, Sum, Q
from django.shortcuts import get_object_or_404, render


class ProjectDetailView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = 'kanban/board.html'
    context_object_name = 'project'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)

        project = self.get_object()
        sprint_id = self.request.GET.get('sprint')
        sprints = Sprint.objects.filter(project=project).order_by('-start_time')

        selected_sprint = None
        if sprint_id:
            try:
                selected_sprint = sprints.get(id=sprint_id)
            except Sprint.DoesNotExist:
                selected_sprint = None

        columns = (
            Column.objects
            .filter(project=project)
            .order_by('position')
            .prefetch_related('tasks', 'auditors')
        )

        # Annotate each column with a helper attribute
        for col in columns:
            col.is_auditor = col.auditors.filter(id=self.request.user.id).exists()
            y = 0
        context.update({
            'columns': columns,
            'sprints': sprints,
            'selected_sprint': selected_sprint
        })

        return context


class ProjectListView(LoginRequiredMixin, ListView):
    model = Project
    template_name = 'kanban/project_list.html'
    context_object_name = 'projects'

    def get_queryset(self):
        user = self.request.user
        if isinstance(user, AnonymousUser) or not user.is_authenticated:
            return Project.objects.none()

        return Project.objects.filter(
            Q(owner__user=user) |
            Q(collaborators=user)
        ).distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)
        return context


class EisenhowerMatrixView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = "kanban/eisenhower.html"
    context_object_name = "project"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)

        project = self.get_object()
        missions = Mission.objects.filter(campaign__project=project)

        urgency_levels = {1: "Low", 2: "Medium", 3: "High"}
        impact_levels = {1: "Low", 2: "Medium", 3: "High"}

        # Build a 3×3 matrix as a list of rows
        matrix = []
        for u, u_label in urgency_levels.items():
            row = []
            for i, i_label in impact_levels.items():
                cell_missions = missions.filter(urgency=u, impact=i)
                row.append({
                    "urgency": u_label,
                    "impact": i_label,
                    "missions": cell_missions,
                })
            matrix.append({"urgency": u_label, "cells": row})

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
        # Prefetch campaigns and tasks for efficiency
        missions = (
            Mission.objects.filter(campaign__project=project)
            .select_related("campaign")
            .prefetch_related("tasks")
        )

        context.update({
            "missions": missions,
        })
        return context


class TaskCreateView(CreateView):
    model = Task
    form_class = TaskCreateForm
    template_name = "kanban/create_task.html"
    context_object_name = "task"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(Project, pk=kwargs["pk"])

        # If ?column=ID is passed, validate it
        column_id = request.GET.get("column")
        if column_id:
            column = get_object_or_404(Column, pk=column_id, project=self.project)
            if not column.can_add_task:
                return HttpResponseForbidden("You cannot add tasks to this column.")

        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # Pass project to the form so it can filter missions/columns/sprints
        kwargs["project"] = self.project
        return kwargs

    def form_valid(self, form):
        # Assign project-related fields automatically
        task = form.save(commit=False)

        # If column was passed in URL (?column=3), preselect it
        column_id = self.request.GET.get("column")
        if column_id:
            task.column = get_object_or_404(Column, pk=column_id, project=self.project)

        task.save()
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("kanban:project_detail", kwargs={"pk": self.project.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["project"] = self.project
        return PageProcessor().decorate(context, self.request)


class TaskUpdateView(UpdateView):
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

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.project
        return kwargs

    def get_success_url(self):
        return reverse("kanban:project_detail", kwargs={"pk": self.project.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["project"] = self.project
        return PageProcessor().decorate(context, self.request)


class TaskDeleteView(DeleteView):
    model = Task
    template_name = "kanban/delete_task.html"
    context_object_name = "task"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(Project, pk=kwargs["project_pk"])
        task = self.get_object()

        if not task.column.can_add_task:
            return HttpResponseForbidden("You cannot delete tasks in this column.")

        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return reverse("kanban:project_detail", kwargs={"pk": self.project.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["project"] = self.project
        return PageProcessor().decorate(context, self.request)


def _get_adjacent_column(task, direction):
    """Helper: find next or previous column based on position."""
    current_pos = task.column.position
    project = task.column.project

    if direction == "next":
        return (
            Column.objects
            .filter(project=project, position__gt=current_pos)
            .order_by("position")
            .first()
        )
    else:
        return (
            Column.objects
            .filter(project=project, position__lt=current_pos)
            .order_by("-position")
            .first()
        )


@login_required
def promote_task(request, project_id, task_id):
    project = get_object_or_404(Project, id=project_id)
    task = get_object_or_404(Task, id=task_id, mission__campaign__project=project)

    next_column = _get_adjacent_column(task, "next")
    if not next_column:
        messages.warning(request, "Task is already in the last column.")
        return redirect("kanban:project_detail", pk=project_id)

    # Permission check
    if not next_column.auditors.filter(id=request.user.id).exists():
        messages.error(request, "You are not allowed to promote tasks into this column.")
        return redirect("kanban:project_detail", pk=project_id)

    task.column = next_column
    task.save()

    messages.success(request, f"Task promoted to {next_column.name}.")
    return redirect("kanban:project_detail", pk=project_id)


@login_required
def demote_task(request, project_id, task_id):
    project = get_object_or_404(Project, id=project_id)
    task = get_object_or_404(Task, id=task_id, mission__campaign__project=project)

    prev_column = _get_adjacent_column(task, "prev")
    if not prev_column:
        messages.warning(request, "Task is already in the first column.")
        return redirect("kanban:project_detail", pk=project_id)

    # Permission check
    if not prev_column.auditors.filter(id=request.user.id).exists():
        messages.error(request, "You are not allowed to demote tasks into this column.")
        return redirect("kanban:project_detail", pk=project_id)

    task.column = prev_column
    task.save()

    messages.success(request, f"Task moved back to {prev_column.name}.")
    return redirect("kanban:project_detail", pk=project_id)


@login_required
def sprint_metrics(request, pk):
    project = get_object_or_404(Project, pk=pk)

    tasks = (
        Task.objects
        .filter(column__project=project)
        .select_related("column", "sprint", "assignee")
    )

    sprints = (
        Sprint.objects
        .filter(project=project)
        .order_by("-start_time")
    )

    done_filter = (
        Q(column__name__iexact="done") |
        Q(column__name__iexact="complete") |
        Q(column__name__iexact="completed")
    )

    sprint_items = []

    for sprint in sprints:
        sprint_tasks = tasks.filter(sprint=sprint)

        total_tasks = sprint_tasks.count()
        completed_tasks = sprint_tasks.filter(done_filter).count()
        open_tasks = total_tasks - completed_tasks
        total_weight = sprint_tasks.aggregate(total=Sum("weight"))["total"] or 0
        completed_weight = sprint_tasks.filter(done_filter).aggregate(total=Sum("weight"))["total"] or 0

        completion_rate = round((completed_tasks / total_tasks) * 100, 1) if total_tasks else 0
        weight_completion_rate = round((completed_weight / total_weight) * 100, 1) if total_weight else 0

        sprint_items.append({
            "id": sprint.id,
            "name": sprint.name,
            "start": sprint.start_time,
            "end": sprint.end_time,
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "open_tasks": open_tasks,
            "total_weight": total_weight,
            "completed_weight": completed_weight,
            "completion_rate": completion_rate,
            "weight_completion_rate": weight_completion_rate,
        })

    total_sprints = len(sprint_items)
    total_tasks = sum(item["total_tasks"] for item in sprint_items)
    completed_tasks = sum(item["completed_tasks"] for item in sprint_items)
    total_weight = sum(item["total_weight"] for item in sprint_items)
    completed_weight = sum(item["completed_weight"] for item in sprint_items)

    overall_completion_rate = round((completed_tasks / total_tasks) * 100, 1) if total_tasks else 0
    overall_weight_completion_rate = round((completed_weight / total_weight) * 100, 1) if total_weight else 0

    chart_colors = [
        "#4f5fa1",
        "#2f3d63",
        "#5fa38c",
        "#4a8f7a",
        "#ff4455",
        "#d94a4a",
    ]

    sprint_completion_chart = {
        "chart_type": "bar",
        "labels": [item["name"] for item in sprint_items],
        "datasets": [{
            "label": "Completion %",
            "data": [item["completion_rate"] for item in sprint_items],
            "backgroundColor": chart_colors,
        }],
        "options": {
            "scales": {
                "y": {
                    "beginAtZero": True,
                    "max": 100,
                }
            }
        }
    }

    sprint_task_chart = {
        "chart_type": "bar",
        "labels": [item["name"] for item in sprint_items],
        "datasets": [
            {
                "label": "Completed",
                "data": [item["completed_tasks"] for item in sprint_items],
                "backgroundColor": "#5fa38c",
            },
            {
                "label": "Open",
                "data": [item["open_tasks"] for item in sprint_items],
                "backgroundColor": "#4f5fa1",
            },
        ],
        "options": {
            "scales": {
                "x": {"stacked": True},
                "y": {
                    "stacked": True,
                    "beginAtZero": True,
                },
            }
        }
    }

    context = {
        "project": project,

        "sprint_items": sprint_items,

        "total_sprints": total_sprints,
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "open_tasks": total_tasks - completed_tasks,
        "total_weight": total_weight,
        "completed_weight": completed_weight,
        "overall_completion_rate": overall_completion_rate,
        "overall_weight_completion_rate": overall_weight_completion_rate,

        "sprint_completion_chart_json": json.dumps(sprint_completion_chart),
        "sprint_task_chart_json": json.dumps(sprint_task_chart),
    }

    return render(
        request,
        "kanban/sprint_metrics.html",
        PageProcessor().decorate(context, request)
    )


class MissionDetailView(LoginRequiredMixin, DetailView):
    model = Mission
    template_name = "kanban/mission_detail.html"
    context_object_name = "mission"

    def get_queryset(self):
        return (
            Mission.objects
            .select_related("campaign", "campaign__project", "owner")
            .prefetch_related(
                "tasks",
                "tasks__column",
                "tasks__sprint",
                "tasks__assignee",
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

        context.update({
            "project": project,
            "tasks": tasks,

            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "open_tasks": open_tasks,

            "total_weight": total_weight,
            "completed_weight": completed_weight,

            "completion_rate": completion_rate,
            "weight_completion_rate": weight_completion_rate,
        })

        return context