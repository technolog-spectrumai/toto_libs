import json

from django.core.exceptions import ValidationError
from django.db import transaction
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
from toto.kanban.forms import TaskCreateForm, ProjectTokenizationCreateForm, ProjectTokenizationDefaultForm
from toto.kanban.metrics import SprintMetricsCalculator, MissionMetricsCalculator
from toto.kanban.models import (
    Project, Column, Task, Sprint, Mission, DocumentationPage, Practitioner,
    ProjectTokenization, ProjectTokenizationStatus,
)
from toto.kanban.plugins.mission_plugins import MissionPlugin
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
                "tasks__detection_mitigations",
            )
        )

        for column in columns:
            column.is_auditor = column.auditors.filter(person__user=self.request.user).exists()

        tokenization = getattr(project, "tokenization", None)
        default_form = ProjectTokenizationDefaultForm()

        context.update({
            "columns": columns,
            "sprints": sprints,
            "selected_sprint": selected_sprint,
            "tokenization": tokenization,
            "default_form": default_form,
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
                "tasks__detection_mitigations",
                "tasks__detection_mitigations__category",
                "tasks__detection_mitigations__address",
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


# ---------------------------------------------------------------------------
# Mission Economy
# ---------------------------------------------------------------------------

@login_required
def mission_economy(request, pk):
    mission = get_object_or_404(
        Mission.objects.select_related(
            "campaign__project", "campaign__owner", "owner",
        ),
        pk=pk,
    )
    project = mission.campaign.project

    from toto.contracts.models import Contract, ContractNode

    # Contracts linked to this mission via a ContractNode
    linked_nodes = ContractNode.objects.filter(
        object_app="kanban",
        object_model="mission",
        object_id=str(mission.pk),
    ).select_related("contract")
    linked_contracts = [n.contract for n in linked_nodes]
    linked_contract_ids = {c.pk for c in linked_contracts}

    # Practitioners on this mission (via tasks)
    practitioners = (
        Practitioner.objects
        .filter(assigned_tasks__mission=mission, is_active=True)
        .select_related("person")
        .distinct()
    )

    def _payroll_for(person):
        return list(
            Contract.objects.filter(
                metadata__archetype="payroll",
                nodes__key="worker_person",
                nodes__object_app="people",
                nodes__object_model="person",
                nodes__object_id=str(person.pk),
            ).distinct()
        )

    practitioner_rows = [
        {"practitioner": p, "payroll": _payroll_for(p.person)}
        for p in practitioners
    ]

    # Project tokenization
    tokenization = getattr(project, "tokenization", None)

    # All contracts for the link form (exclude already linked)
    all_contracts = Contract.objects.exclude(pk__in=linked_contract_ids).order_by("name")

    # Financial planning: budget summary per contract
    budget_summary = []
    for contract in linked_contracts:
        meta = contract.metadata or {}
        archetype = meta.get("archetype", "")
        entry = {"contract": contract, "archetype": archetype, "lines": []}
        if archetype == "payroll":
            allocation = meta.get("allocation_base_units")
            asset_unit = meta.get("asset_unit_name", "")
            if allocation is not None:
                entry["lines"].append({"label": "Allocation", "value": f"{allocation} {asset_unit}".strip()})
            frequency = meta.get("frequency", "")
            if frequency:
                entry["lines"].append({"label": "Frequency", "value": frequency.title()})
        elif archetype == "insurance":
            premium = meta.get("premium_amount_base_units")
            asset_unit = meta.get("premium_asset_unit", "")
            if premium is not None:
                entry["lines"].append({"label": "Premium", "value": f"{premium} {asset_unit}".strip()})
            freq = meta.get("premium_frequency", "")
            if freq:
                entry["lines"].append({"label": "Frequency", "value": freq.title()})
        elif archetype == "loan":
            principal = meta.get("principal_base_units")
            asset_unit = meta.get("loan_asset_unit", "")
            if principal is not None:
                entry["lines"].append({"label": "Principal", "value": f"{principal} {asset_unit}".strip()})
            rate_bps = meta.get("interest_rate_bps")
            if rate_bps is not None:
                entry["lines"].append({"label": "Rate", "value": f"{rate_bps / 100:.2f}%"})
        budget_summary.append(entry)

    # Cost history: recent ContractEvents for linked contracts
    from toto.claims.models import ContractEvent
    cost_history = (
        ContractEvent.objects
        .filter(contract_id__in=linked_contract_ids)
        .select_related("contract", "actor")
        .order_by("-created_at")[:20]
    ) if linked_contract_ids else []

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "link":
            contract_id = request.POST.get("contract_id")
            if contract_id:
                contract = get_object_or_404(Contract, pk=contract_id)
                ContractNode.objects.get_or_create(
                    contract=contract,
                    key="linked_mission",
                    object_app="kanban",
                    object_model="mission",
                    object_id=str(mission.pk),
                    defaults={
                        "node_type": "manual",
                        "title": f"Mission: {mission.title}",
                        "is_manual": True,
                    },
                )
                messages.success(request, f'Contract "{contract.name}" linked to mission.')

        elif action == "unlink":
            contract_id = request.POST.get("contract_id")
            ContractNode.objects.filter(
                contract_id=contract_id,
                object_app="kanban",
                object_model="mission",
                object_id=str(mission.pk),
            ).delete()
            messages.success(request, "Contract unlinked.")

        return redirect("kanban:mission_economy", pk=pk)

    context = PageProcessor().decorate({
        "mission": mission,
        "project": project,
        "linked_contracts": linked_contracts,
        "all_contracts": all_contracts,
        "practitioner_rows": practitioner_rows,
        "tokenization": tokenization,
        "budget_summary": budget_summary,
        "cost_history": cost_history,
    }, request)
    return render(request, "kanban/mission_economy.html", context)


# ---------------------------------------------------------------------------
# Project Tokenization
# ---------------------------------------------------------------------------

@login_required
def project_tokenize(request, pk):
    project = get_object_or_404(Project.objects.select_related("project_lead"), pk=pk)

    existing = getattr(project, "tokenization", None)
    if existing:
        messages.info(request, "This project is already tokenized.")
        return redirect("assets:asset_detail", pk=existing.asset_id)

    if request.method == "POST":
        form = ProjectTokenizationCreateForm(request.POST, project=project)
        if form.is_valid():
            data = form.cleaned_data
            metadata = data.get("metadata") or {}
            try:
                with transaction.atomic():
                    from toto.assets.services.assets import create_asset
                    asset = create_asset(
                        name=data["asset_name"],
                        unit_name=data["unit_name"],
                        total_supply=data["total_supply"],
                        decimals=data["decimals"],
                        reserve_account=data["reserve_account"],
                        reference=f"project-{project.pk}-{data['unit_name'].lower()}",
                        description=f"Token for project: {project.name}",
                        metadata={
                            **metadata,
                            "tokenized_project_id": project.pk,
                            "tokenized_project_name": project.name,
                        },
                    )
                    asset.is_currency = data.get("is_currency", False)
                    asset.backing_document = data.get("backing_document", "")
                    asset.minting_authority = data.get("minting_authority", "")
                    asset.save(update_fields=["is_currency", "backing_document", "minting_authority", "updated_at"])
                    ProjectTokenization.objects.create(
                        project=project,
                        asset=asset,
                        supervisor=data.get("supervisor"),
                        metadata=metadata,
                    )
            except ValidationError as exc:
                form.add_error(None, exc.messages[0] if hasattr(exc, "messages") else str(exc))
            else:
                messages.success(request, f"{project.name} tokenized as {asset.unit_name}.")
                return redirect("assets:asset_detail", pk=asset.pk)
    else:
        slug = "".join(ch for ch in project.name.upper() if ch.isalnum())[:12] or f"PRJ{project.pk}"
        form = ProjectTokenizationCreateForm(
            project=project,
            initial={
                "asset_name": f"{project.name} Token",
                "unit_name": slug,
                "decimals": 0,
                "total_supply": 1000000,
                "backing_document": project.description,
            },
        )

    context = PageProcessor().decorate({"project": project, "form": form}, request)
    return render(request, "kanban/project_tokenize.html", context)


@require_POST
@login_required
def project_tokenization_default(request, pk):
    tokenization = get_object_or_404(
        ProjectTokenization.objects.select_related("asset", "project"),
        pk=pk,
    )

    if tokenization.status == ProjectTokenizationStatus.DEFAULTED:
        messages.warning(request, "Tokenization is already defaulted.")
        return redirect("kanban:project_detail", pk=tokenization.project_id)

    form = ProjectTokenizationDefaultForm(request.POST)
    if form.is_valid():
        tokenization.status = ProjectTokenizationStatus.DEFAULTED
        tokenization.default_reason = form.cleaned_data["reason"]
        tokenization.default_note = form.cleaned_data.get("note", "")
        tokenization.defaulted_at = timezone.now()
        tokenization.defaulted_by = request.user
        tokenization.save(update_fields=["status", "default_reason", "default_note", "defaulted_at", "defaulted_by"])
        messages.success(request, f"Tokenization for {tokenization.project.name} has been defaulted.")

    return redirect("kanban:project_detail", pk=tokenization.project_id)
