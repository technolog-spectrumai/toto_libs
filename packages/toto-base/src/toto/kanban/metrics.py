from datetime import timedelta

from django.db.models import Sum

from toto.kanban.models import Task, Sprint, Mission


class BaseMetricsCalculator:
    def __init__(self, project):
        self.project = project
        self.tasks = self.get_project_tasks()

    def get_project_tasks(self):
        return (
            Task.objects
            .filter(mission__campaign__project=self.project)
            .select_related(
                "mission",
                "mission__campaign",
                "column",
                "sprint",
                "assignee__person",
            )
        )

    def percent(self, part, total):
        return round((part / total) * 100, 1) if total else 0

    def weight_sum(self, queryset):
        return queryset.aggregate(total=Sum("weight"))["total"] or 0

    def completed_tasks(self, queryset):
        return queryset.filter(completed_at__isnull=False)

    def open_tasks(self, queryset):
        return queryset.filter(completed_at__isnull=True)

    def task_summary(self, queryset):
        total_tasks = queryset.count()
        completed_tasks = self.completed_tasks(queryset).count()
        open_tasks = total_tasks - completed_tasks

        total_weight = self.weight_sum(queryset)
        completed_weight = self.weight_sum(self.completed_tasks(queryset))
        open_weight = total_weight - completed_weight

        return {
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "open_tasks": open_tasks,
            "total_weight": total_weight,
            "completed_weight": completed_weight,
            "open_weight": open_weight,
            "completion_rate": self.percent(completed_tasks, total_tasks),
            "weight_completion_rate": self.percent(completed_weight, total_weight),
        }


class SprintMetricsCalculator(BaseMetricsCalculator):
    def __init__(self, project):
        super().__init__(project)

        self.sprints = (
            Sprint.objects
            .filter(project=project)
            .order_by("-start_time")
        )

    def get_latest_sprint(self):
        return self.sprints.first()

    def get_sprint_items(self):
        items = []

        for sprint in self.sprints:
            sprint_tasks = self.tasks.filter(sprint=sprint)

            items.append({
                "id": sprint.id,
                "name": sprint.name,
                "start": sprint.start_time,
                "end": sprint.end_time,
                **self.task_summary(sprint_tasks),
            })

        return items

    def get_summary(self):
        sprint_items = self.get_sprint_items()

        total_sprints = len(sprint_items)
        total_tasks = sum(item["total_tasks"] for item in sprint_items)
        completed_tasks = sum(item["completed_tasks"] for item in sprint_items)
        open_tasks = sum(item["open_tasks"] for item in sprint_items)

        total_weight = sum(item["total_weight"] for item in sprint_items)
        completed_weight = sum(item["completed_weight"] for item in sprint_items)
        open_weight = sum(item["open_weight"] for item in sprint_items)

        return {
            "sprint_items": sprint_items,
            "total_sprints": total_sprints,
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "open_tasks": open_tasks,
            "total_weight": total_weight,
            "completed_weight": completed_weight,
            "open_weight": open_weight,
            "overall_completion_rate": self.percent(completed_tasks, total_tasks),
            "overall_weight_completion_rate": self.percent(completed_weight, total_weight),
        }

    def get_burndown_labels(self, sprint=None):
        sprint = sprint or self.get_latest_sprint()

        if not sprint:
            return []

        days = (sprint.end_time.date() - sprint.start_time.date()).days + 1

        return [
            (sprint.start_time.date() + timedelta(days=i)).strftime("%b %d")
            for i in range(days)
        ]

    def get_burndown_data(self, sprint=None):
        sprint = sprint or self.get_latest_sprint()

        if not sprint:
            return []

        sprint_tasks = self.tasks.filter(sprint=sprint)
        total_weight = self.weight_sum(sprint_tasks)

        days = (sprint.end_time.date() - sprint.start_time.date()).days + 1

        data = []

        for i in range(days):
            day = sprint.start_time.date() + timedelta(days=i)

            completed_weight = (
                sprint_tasks
                .filter(completed_at__date__lte=day)
                .aggregate(done=Sum("weight"))["done"]
                or 0
            )

            data.append(max(total_weight - completed_weight, 0))

        return data

    def get_velocity_labels(self):
        return [
            sprint.name
            for sprint in self.sprints.order_by("start_time")
        ]

    def get_velocity_data(self):
        return [
            self.weight_sum(
                self.tasks.filter(
                    sprint=sprint,
                    completed_at__isnull=False,
                )
            )
            for sprint in self.sprints.order_by("start_time")
        ]

    def get_lead_tasks(self):
        return (
            self.tasks
            .filter(
                completed_at__isnull=False,
                sprint__isnull=False,
            )
            .select_related("sprint")
        )

    def get_lead_labels(self):
        return [
            task.title
            for task in self.get_lead_tasks()
        ]

    def get_lead_data(self):
        return [
            (task.completed_at.date() - task.sprint.start_time.date()).days
            for task in self.get_lead_tasks()
        ]

    def get_assignee_items(self):
        assignees = (
            self.tasks
            .values("assignee_id", "assignee__person__display_name")
            .distinct()
            .order_by("assignee__person__display_name")
        )

        items = []

        for assignee in assignees:
            assignee_id = assignee["assignee_id"]

            if assignee_id:
                assignee_tasks = self.tasks.filter(assignee_id=assignee_id)
            else:
                assignee_tasks = self.tasks.filter(assignee__isnull=True)

            items.append({
                "label": assignee["assignee__person__display_name"] or "Unassigned",
                **self.task_summary(assignee_tasks),
            })

        return items

    def get_campaign_progress(self):
        data = []

        for campaign in self.project.campaigns.all():
            campaign_tasks = self.tasks.filter(mission__campaign=campaign)
            summary = self.task_summary(campaign_tasks)

            data.append({
                "label": campaign.name,
                "total": summary["total_weight"],
                "done": summary["completed_weight"],
                "pct": summary["weight_completion_rate"],
            })

        return data

    def get_context_data(self):
        return {
            **self.get_summary(),
            "latest_sprint": self.get_latest_sprint(),

            "burndown_labels": self.get_burndown_labels(),
            "burndown_data": self.get_burndown_data(),

            "velocity_labels": self.get_velocity_labels(),
            "velocity_data": self.get_velocity_data(),

            "lead_labels": self.get_lead_labels(),
            "lead_data": self.get_lead_data(),

            "assignee_items": self.get_assignee_items(),
            "campaign_progress_data": self.get_campaign_progress(),
        }


class MissionMetricsCalculator(BaseMetricsCalculator):
    def __init__(self, project):
        super().__init__(project)

        self.missions = (
            Mission.objects
            .filter(campaign__project=project)
            .select_related("campaign", "owner")
            .prefetch_related(
                "tasks",
                "tasks__column",
                "tasks__sprint",
                "tasks__assignee__person",
            )
            .order_by("campaign__name", "title")
        )

    def get_mission_items(self):
        items = []

        for mission in self.missions:
            mission_tasks = self.tasks.filter(mission=mission)
            summary = self.task_summary(mission_tasks)

            items.append({
                "mission": mission,
                "tasks": mission_tasks,
                "title": mission.title,
                "campaign": mission.campaign.name,
                "urgency": mission.urgency_label,
                "impact": mission.impact_label,
                **summary,
            })

        return items

    def get_summary(self):
        mission_items = self.get_mission_items()

        total_missions = len(mission_items)
        total_tasks = sum(item["total_tasks"] for item in mission_items)
        completed_tasks = sum(item["completed_tasks"] for item in mission_items)
        open_tasks = sum(item["open_tasks"] for item in mission_items)

        total_weight = sum(item["total_weight"] for item in mission_items)
        completed_weight = sum(item["completed_weight"] for item in mission_items)
        open_weight = sum(item["open_weight"] for item in mission_items)

        high_urgency_count = sum(
            1 for item in mission_items
            if item["mission"].urgency == 3
        )

        high_impact_count = sum(
            1 for item in mission_items
            if item["mission"].impact == 3
        )

        return {
            "mission_items": mission_items,
            "total_missions": total_missions,
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "open_tasks": open_tasks,
            "total_weight": total_weight,
            "completed_weight": completed_weight,
            "open_weight": open_weight,
            "overall_completion_rate": self.percent(completed_tasks, total_tasks),
            "overall_weight_completion_rate": self.percent(completed_weight, total_weight),
            "high_urgency_count": high_urgency_count,
            "high_impact_count": high_impact_count,
        }

    def get_context_data(self):
        return {
            **self.get_summary(),
        }