"""Project metrics.

Everything here reads ``Task.completed_at``, which since v1.15 is a derived,
database-enforced consequence of ``Task.status`` — so what a chart says and what
the board shows can no longer drift apart.

The calculators fetch once and group in Python. That is not premature: the old
shape cost one query per sprint, per assignee, per campaign and per burndown
day, which made the query count a function of the data and left nothing stable
to assert against.
"""

from collections import defaultdict
from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone
from django.utils.functional import cached_property

from toto.kanban.models import (
    Task, TaskStatus, Sprint, Mission, visible_missions_for, visible_tasks_for,
)


def percent(part, total):
    return round((part / total) * 100, 1) if total else 0


def _summarize(pairs):
    """Fold (weight, completed_at) pairs into the summary shape. No queries."""
    total_tasks = completed_tasks = total_weight = completed_weight = 0

    for weight, completed_at in pairs:
        weight = weight or 0
        total_tasks += 1
        total_weight += weight
        if completed_at is not None:
            completed_tasks += 1
            completed_weight += weight

    return {
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "open_tasks": total_tasks - completed_tasks,
        "total_weight": total_weight,
        "completed_weight": completed_weight,
        "open_weight": total_weight - completed_weight,
        "completion_rate": percent(completed_tasks, total_tasks),
        "weight_completion_rate": percent(completed_weight, total_weight),
    }


def summarize_tasks(tasks):
    """Summary for Task instances already in memory.

    Lets a view that has prefetched its tasks stop throwing the prefetch away to
    recompute the same numbers with four more queries.
    """
    return _summarize((task.weight, task.completed_at) for task in tasks)


def summarize_rows(rows):
    return _summarize((row["weight"], row["completed_at"]) for row in rows)


class BaseMetricsCalculator:
    def __init__(self, project, user=None):
        #: user=None means unfiltered — programmatic callers and existing
        #: tests keep their exact numbers; views pass request.user so the
        #: charts agree with the board the same person sees.
        self.project = project
        self.user = user
        self.tasks = self.get_project_tasks()

    def get_project_tasks(self):
        qs = (
            Task.objects
            .filter(mission__campaign__project=self.project)
            .select_related(
                "mission",
                "mission__campaign",
                "sprint",
                "assignee__person",
            )
        )
        if self.user is not None:
            qs = visible_tasks_for(self.user, qs)
        return qs

    def percent(self, part, total):
        return percent(part, total)

    def weight_sum(self, queryset):
        return queryset.aggregate(total=Sum("weight"))["total"] or 0

    def completed_tasks(self, queryset):
        return queryset.filter(completed_at__isnull=False)

    def open_tasks(self, queryset):
        return queryset.filter(completed_at__isnull=True)

    def task_summary(self, queryset):
        return _summarize(queryset.values_list("weight", "completed_at"))


class SprintMetricsCalculator(BaseMetricsCalculator):
    #: One flat row per task, fetched once. `.values()` drops the select_related
    #: joins it does not need, so this is a single query however wide the page.
    ROW_FIELDS = (
        "id", "title", "weight", "completed_at", "status",
        "sprint_id",
        "assignee_id", "assignee__person__display_name",
        "mission__campaign_id",
    )

    def __init__(self, project, sprint_id=None, user=None):
        super().__init__(project, user=user)
        self.sprint_id = sprint_id

    @cached_property
    def sprints(self):
        return list(Sprint.objects.filter(project=self.project).order_by("-start_time"))

    @cached_property
    def rows(self):
        return list(self.tasks.values(*self.ROW_FIELDS))

    @cached_property
    def campaigns(self):
        # Its own query so campaigns holding no tasks still appear, at 0%.
        return list(self.project.campaigns.all().values("id", "name"))

    def get_latest_sprint(self):
        return self.sprints[0] if self.sprints else None

    @cached_property
    def selected_sprint(self):
        if self.sprint_id is not None:
            for sprint in self.sprints:
                if str(sprint.pk) == str(self.sprint_id):
                    return sprint
        return self.get_latest_sprint()

    # ── summaries ────────────────────────────────────────────────────────────

    def get_sprint_items(self):
        by_sprint = defaultdict(list)
        for row in self.rows:
            if row["sprint_id"] is not None:
                by_sprint[row["sprint_id"]].append(row)

        return [
            {
                "id": sprint.id,
                "name": sprint.name,
                "start": sprint.start_time,
                "end": sprint.end_time,
                **summarize_rows(by_sprint.get(sprint.pk, [])),
            }
            for sprint in self.sprints
        ]

    def get_summary(self):
        """Project-wide totals, plus the per-sprint breakdown.

        The headline figures count every task in the project. They used to sum
        over sprints only, so work sitting in the backlog was missing from
        "Total tasks" while the assignee and campaign charts on the same page
        counted it — the page contradicted itself, and both halves contradicted
        the board.
        """
        overall = summarize_rows(self.rows)
        sprint_items = self.get_sprint_items()

        return {
            "sprint_items": sprint_items,
            "total_sprints": len(sprint_items),
            **overall,
            "overall_completion_rate": overall["completion_rate"],
            "overall_weight_completion_rate": overall["weight_completion_rate"],
            "sprinted_tasks": sum(item["total_tasks"] for item in sprint_items),
            "backlog_tasks": overall["total_tasks"] - sum(
                item["total_tasks"] for item in sprint_items
            ),
        }

    def get_status_counts(self):
        counts = {
            value: {"status": value, "label": str(label), "tasks": 0, "weight": 0}
            for value, label in TaskStatus.choices
        }
        for row in self.rows:
            bucket = counts.get(row["status"])
            if bucket is None:
                # An unrecognised value should be visible, not silently dropped.
                bucket = counts.setdefault(
                    row["status"],
                    {"status": row["status"], "label": row["status"], "tasks": 0, "weight": 0},
                )
            bucket["tasks"] += 1
            bucket["weight"] += row["weight"] or 0
        return list(counts.values())

    # ── burndown ─────────────────────────────────────────────────────────────

    def get_burndown(self, sprint=None):
        """Remaining sprint weight per local day, against a linear guideline.

        Returns ``{"labels", "actual", "ideal"}`` — three lists of equal length,
        one entry per calendar day of the sprint inclusive.

        ``actual[i]`` is the weight still open at the end of local day ``i``, and
        ``None`` for days after today, so the line stops at the present. Drawing
        every sprint day meant a sprint ending in two weeks trailed a flat line
        to the right edge, which reads as "work has stalled" rather than "this
        has not happened yet".

        ``ideal[i]`` runs from the sprint's *opening* scope — total weight less
        whatever was already finished when the sprint opened — down to zero on
        the last day. Both series therefore start at the same height, and the
        chart becomes readable as actual-against-plan rather than a single line
        with nothing to judge it by.

        Non-goal: scope changes. Scope is the sprint's membership *now*, because
        nothing records when a task joined or left one. A task added on day six
        raises the whole historical line. Answering "what did the board look
        like on day three" needs a (task, sprint, added_at, removed_at) table.
        """
        sprint = sprint or self.selected_sprint

        if not sprint:
            return {"labels": [], "actual": [], "ideal": []}

        # Local dates on both sides. Labels used to come from the UTC date of an
        # aware datetime while buckets came from the database's own timezone
        # conversion, so the two disagreed by a day near midnight.
        start = timezone.localtime(sprint.start_time).date()
        end = max(timezone.localtime(sprint.end_time).date(), start)
        today = timezone.localdate()
        days = (end - start).days + 1

        total_weight = 0
        burned_before = 0
        burned_on = defaultdict(int)

        for row in self.rows:
            if row["sprint_id"] != sprint.pk:
                continue

            weight = row["weight"] or 0
            total_weight += weight

            completed_at = row["completed_at"]
            if completed_at is None:
                continue

            day = timezone.localtime(completed_at).date()
            if day < start:
                burned_before += weight
            else:
                # Anything past `end` lands here and is never plotted, which is
                # correct: it was not burned down during the sprint.
                burned_on[day] += weight

        opening = total_weight - burned_before
        step = opening / (days - 1) if days > 1 else opening

        labels, actual, ideal = [], [], []
        remaining = opening

        for index in range(days):
            day = start + timedelta(days=index)
            remaining -= burned_on.get(day, 0)

            labels.append(day.strftime("%b %d"))
            actual.append(None if day > today else remaining)
            ideal.append(round(opening - step * index, 2) if days > 1 else 0.0)

        return {"labels": labels, "actual": actual, "ideal": ideal}

    # ── velocity, lead, workload ─────────────────────────────────────────────

    def get_velocity(self):
        """Weight completed *inside* each sprint's window, oldest sprint first.

        Bounding by the window is what stops a closed sprint's velocity from
        creeping upward every time an old task of its is finally ticked off.
        """
        windows = {sprint.pk: (sprint.start_time, sprint.end_time) for sprint in self.sprints}
        done = defaultdict(int)

        for row in self.rows:
            window = windows.get(row["sprint_id"])
            completed_at = row["completed_at"]
            if window is None or completed_at is None:
                continue
            start, end = window
            if start <= completed_at <= end:
                done[row["sprint_id"]] += row["weight"] or 0

        ordered = sorted(self.sprints, key=lambda sprint: sprint.start_time)
        return {
            "labels": [sprint.name for sprint in ordered],
            "data": [done.get(sprint.pk, 0) for sprint in ordered],
        }

    def get_days_to_completion(self, limit=20):
        """Days from sprint start to completion, most recent `limit` tasks.

        This is **not** lead time, though it was labelled as such. Lead time runs
        from when work was requested, and ``Task`` records no creation timestamp
        — ``DomainEntity`` carries only a uid — so it is not derivable here at
        all. Add ``created_at`` to ``Task`` if the real metric is wanted.
        """
        starts = {sprint.pk: sprint.start_time for sprint in self.sprints}
        finished = [
            (row["completed_at"], row["title"], starts[row["sprint_id"]])
            for row in self.rows
            if row["completed_at"] is not None and row["sprint_id"] in starts
        ]
        # Newest first to pick the window, then back into chronological order.
        recent = sorted(sorted(finished, key=lambda item: item[0], reverse=True)[:limit])

        return {
            "labels": [title for _, title, _ in recent],
            "data": [
                (timezone.localtime(done).date() - timezone.localtime(start).date()).days
                for done, _, start in recent
            ],
        }

    def get_assignee_items(self):
        by_assignee = defaultdict(list)
        names = {}
        for row in self.rows:
            key = row["assignee_id"]
            by_assignee[key].append(row)
            names[key] = row["assignee__person__display_name"] or "Unassigned"

        return [
            {"label": names[key], **summarize_rows(rows)}
            for key, rows in sorted(by_assignee.items(), key=lambda item: names[item[0]])
        ]

    def get_campaign_progress(self):
        by_campaign = defaultdict(list)
        for row in self.rows:
            by_campaign[row["mission__campaign_id"]].append(row)

        data = []
        for campaign in self.campaigns:
            summary = summarize_rows(by_campaign.get(campaign["id"], []))
            data.append({
                "label": campaign["name"],
                "total": summary["total_weight"],
                "done": summary["completed_weight"],
                "pct": summary["weight_completion_rate"],
            })
        return data

    def get_context_data(self):
        burndown = self.get_burndown()
        velocity = self.get_velocity()
        days_to_completion = self.get_days_to_completion()

        return {
            **self.get_summary(),
            "latest_sprint": self.get_latest_sprint(),

            "burndown": burndown,
            "burndown_labels": burndown["labels"],
            "burndown_data": burndown["actual"],
            "burndown_ideal": burndown["ideal"],

            "velocity_labels": velocity["labels"],
            "velocity_data": velocity["data"],

            "lead_labels": days_to_completion["labels"],
            "lead_data": days_to_completion["data"],

            "status_counts": self.get_status_counts(),
            "assignee_items": self.get_assignee_items(),
            "campaign_progress_data": self.get_campaign_progress(),
        }


class MissionMetricsCalculator(BaseMetricsCalculator):
    def __init__(self, project, user=None):
        super().__init__(project, user=user)

        missions = Mission.objects.filter(campaign__project=project)
        if user is not None:
            missions = visible_missions_for(user, missions)
        self.missions = (
            missions
            .select_related("campaign", "owner")
            .prefetch_related(
                "tasks",
                "tasks__sprint",
                "tasks__assignee__person",
            )
            .order_by("campaign__name", "title")
        )

    def get_mission_items(self):
        items = []

        for mission in self.missions:
            # Read the prefetch rather than re-querying it away.
            mission_tasks = list(mission.tasks.all())

            items.append({
                "mission": mission,
                "tasks": mission_tasks,
                "title": mission.title,
                "campaign": mission.campaign.name,
                "urgency": mission.urgency_label,
                "impact": mission.impact_label,
                **summarize_tasks(mission_tasks),
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
            "overall_completion_rate": percent(completed_tasks, total_tasks),
            "overall_weight_completion_rate": percent(completed_weight, total_weight),
            "high_urgency_count": high_urgency_count,
            "high_impact_count": high_impact_count,
        }

    def get_context_data(self):
        return {
            **self.get_summary(),
        }
