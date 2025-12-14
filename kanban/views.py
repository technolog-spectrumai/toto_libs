import json

from django.views.generic import DetailView, ListView
from django.db.models import Q
from django.contrib.auth.models import AnonymousUser
from kanban.models import Project, Column, Task, Sprint, Mission
from oya.page import PageProcessor
from django.contrib.auth.mixins import LoginRequiredMixin
from oya.page import PageProcessor
from django.db.models import Sum
from datetime import timedelta
from django.utils import timezone


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

        columns = Column.objects.filter(project=project).order_by('position').prefetch_related('tasks')

        context.update({
            'columns': columns,
            'sprints': sprints,
            'selected_sprint': selected_sprint
        })
        return context


class ProjectListView(LoginRequiredMixin, ListView):
    model = Project
    template_name = 'kanban/board_list.html'
    context_object_name = 'projects'

    def get_queryset(self):
        user = self.request.user
        if isinstance(user, AnonymousUser) or not user.is_authenticated:
            return Project.objects.none()

        return Project.objects.filter(
            Q(owner=user) |
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


class MetricsView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = "kanban/metrics.html"
    context_object_name = "project"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)

        project = self.get_object()
        sprint = Sprint.objects.filter(project=project).order_by("-start_time").first()

        # Burndown (using completed_at)
        burndown_labels, burndown_data = [], []
        if sprint:
            total_weight = sprint.tasks.aggregate(total=Sum("weight"))["total"] or 0
            days = (sprint.end_time.date() - sprint.start_time.date()).days + 1
            for i in range(days):
                day = sprint.start_time.date() + timedelta(days=i)
                burndown_labels.append(day.strftime("%b %d"))
                completed_weight = (
                    sprint.tasks.filter(completed_at__date__lte=day)
                    .aggregate(done=Sum("weight"))["done"] or 0
                )
                burndown_data.append(max(total_weight - completed_weight, 0))

        # Incomplete tasks per assignee
        incomplete_labels, incomplete_weights = [], []
        tasks_incomplete = (
            Task.objects.filter(mission__campaign__project=project, completed_at__isnull=True)
            .values("assignee__username")
            .annotate(total_weight=Sum("weight"))
        )
        for row in tasks_incomplete:
            incomplete_labels.append(row["assignee__username"] or "Unassigned")
            incomplete_weights.append(row["total_weight"] or 0)

        # Velocity: completed weight per sprint
        velocity_labels, velocity_data = [], []
        for sp in Sprint.objects.filter(project=project).order_by("start_time"):
            velocity_labels.append(sp.name)
            completed_weight = (
                sp.tasks.filter(completed_at__isnull=False)
                .aggregate(done=Sum("weight"))["done"] or 0
            )
            velocity_data.append(completed_weight)

        # Lead time: average days from creation → completion
        lead_labels, lead_data = [], []
        tasks_completed = Task.objects.filter(mission__campaign__project=project, completed_at__isnull=False)
        for task in tasks_completed:
            if task.completed_at:
                lead_labels.append(task.title)
                lead_data.append((task.completed_at.date() - task.sprint.start_time.date()).days)

        context.update({
            "sprint": sprint,
            "burndown_labels": json.dumps(burndown_labels),
            "burndown_data": json.dumps(burndown_data),
            "incomplete_labels": json.dumps(incomplete_labels),
            "incomplete_weights": json.dumps(incomplete_weights),
            "velocity_labels": json.dumps(velocity_labels),
            "velocity_data": json.dumps(velocity_data),
            "lead_labels": json.dumps(lead_labels),
            "lead_data": json.dumps(lead_data),
        })
        return context





