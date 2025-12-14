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
    template_name = "webfront/metrics.html"
    context_object_name = "project"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_object()

        # decorate base context
        context = PageProcessor().decorate(context, self.request)

        if project.metrics_page:
            page = project.metrics_page
            charts_data = page.get_chart_data(context={
                "user": {
                    "username": self.request.user.username if self.request.user.is_authenticated else None,
                    "is_authenticated": self.request.user.is_authenticated,
                },
                "method": self.request.method,
                "path": self.request.path,
                "query_params": self.request.GET.dict(),
                "project_id": project.id,
            })

            # mutate page-like object for consistency
            page.body = project.description or ""

            context.update({
                "page": page,
                "charts": charts_data
            })

        return context




