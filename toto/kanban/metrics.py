import json
from django.views.generic import DetailView
from django.db.models import Q
from toto.kanban.models import Project, Column, Task, Sprint, Mission
from toto.core.page import PageProcessor
from django.contrib.auth.mixins import LoginRequiredMixin
from toto.core.page import PageProcessor
from django.db.models import Sum
from datetime import timedelta


class MetricsView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = "kanban/metrics.html"
    context_object_name = "project"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)

        project = self.get_object()
        sprint = Sprint.objects.filter(project=project).order_by("-start_time").first()

        context.update({
            "sprint": sprint,
            "burndown_labels": json.dumps(self.get_burndown_labels(sprint)),
            "burndown_data": json.dumps(self.get_burndown_data(sprint)),
            "velocity_labels": json.dumps(self.get_velocity_labels(project)),
            "velocity_data": json.dumps(self.get_velocity_data(project)),
            "lead_labels": json.dumps(self.get_lead_labels(project)),
            "lead_data": json.dumps(self.get_lead_data(project)),
            "assignee_labels": json.dumps(self.get_assignee_labels(project)),
            "completed_counts": json.dumps(self.get_completed_counts(project)),
            "incomplete_counts": json.dumps(self.get_incomplete_counts(project)),
            "campaign_progress_data": self.get_campaign_progress(project),
        })
        return context

    # --- Helpers ---

    def get_burndown_labels(self, sprint):
        if not sprint:
            return []
        days = (sprint.end_time.date() - sprint.start_time.date()).days + 1
        return [(sprint.start_time.date() + timedelta(days=i)).strftime("%b %d") for i in range(days)]

    def get_burndown_data(self, sprint):
        if not sprint:
            return []
        total_weight = sprint.tasks.aggregate(total=Sum("weight"))["total"] or 0
        days = (sprint.end_time.date() - sprint.start_time.date()).days + 1
        data = []
        for i in range(days):
            day = sprint.start_time.date() + timedelta(days=i)
            completed_weight = sprint.tasks.filter(completed_at__date__lte=day).aggregate(done=Sum("weight"))["done"] or 0
            data.append(max(total_weight - completed_weight, 0))
        return data

    def get_velocity_labels(self, project):
        return [sp.name for sp in Sprint.objects.filter(project=project).order_by("start_time")]

    def get_velocity_data(self, project):
        return [
            sp.tasks.filter(completed_at__isnull=False).aggregate(done=Sum("weight"))["done"] or 0
            for sp in Sprint.objects.filter(project=project).order_by("start_time")
        ]

    def get_lead_labels(self, project):
        return [task.title for task in Task.objects.filter(mission__campaign__project=project, completed_at__isnull=False)]

    def get_lead_data(self, project):
        data = []
        for task in Task.objects.filter(mission__campaign__project=project, completed_at__isnull=False):
            if task.sprint and task.completed_at:
                data.append((task.completed_at.date() - task.sprint.start_time.date()).days)
        return data

    def get_assignee_labels(self, project):
        assignees = Task.objects.filter(mission__campaign__project=project).values("assignee__username").distinct()
        return [a["assignee__username"] or "Unassigned" for a in assignees]

    def get_completed_counts(self, project):
        assignees = Task.objects.filter(mission__campaign__project=project).values("assignee__username").distinct()
        return [
            Task.objects.filter(mission__campaign__project=project, assignee__username=a["assignee__username"], completed_at__isnull=False).aggregate(total=Sum("weight"))["total"] or 0
            for a in assignees
        ]

    def get_incomplete_counts(self, project):
        assignees = Task.objects.filter(mission__campaign__project=project).values("assignee__username").distinct()
        return [
            Task.objects.filter(mission__campaign__project=project, assignee__username=a["assignee__username"], completed_at__isnull=True).aggregate(total=Sum("weight"))["total"] or 0
            for a in assignees
        ]

    def get_campaign_progress(self, project):
        data = []
        for campaign in project.campaigns.all():
            total = campaign.missions.aggregate(total=Sum("tasks__weight"))["total"] or 0
            done = campaign.missions.aggregate(done=Sum("tasks__weight", filter=Q(tasks__completed_at__isnull=False)))["done"] or 0
            pct = (done / total * 100) if total > 0 else 0
            data.append({"label": campaign.name, "pct": round(pct, 1)})
        return data





