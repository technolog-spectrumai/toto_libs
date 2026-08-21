"""Metrics tests, with the burndown as the main event.

There were none before. The burndown drew a flat line into the future, bucketed
by UTC date while labelling by local date, and cost a query per sprint day —
none of which anything would have noticed.

Fixtures are built relative to ``timezone.localdate()`` rather than by freezing
the clock: freezegun is not a dependency, and dates-relative-to-today read
better than a mocked now() anyway.
"""

import json
import re
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from toto.core.models import Platform
from toto.kanban.metrics import SprintMetricsCalculator, summarize_tasks
from toto.kanban.models import (
    Campaign, Mission, Practitioner, Project, Sprint, Task, TaskStatus,
)
from toto.people.models import Person

User = get_user_model()


def _local(day, hour=12, minute=0):
    """An aware datetime at a local wall-clock time on `day`."""
    return timezone.make_aware(
        datetime.combine(day, time(hour, minute)), timezone.get_current_timezone()
    )


def _make_project(name="Metrics Project"):
    person = Person.objects.create(display_name=f"Lead {name}", email=f"{name}@x.com")
    project = Project.objects.create(name=name, project_lead=person)
    campaign = Campaign.objects.create(project=project, name=f"{name} campaign")
    mission = Mission.objects.create(campaign=campaign, title=f"{name} mission")
    return project, campaign, mission


def _make_sprint(project, start, end, name="S1"):
    return Sprint.objects.create(name=name, project=project, start_time=start, end_time=end)


def _make_task(mission, *, weight=1, sprint=None, completed_at=None, assignee=None, title="T"):
    """Create a task, planting `completed_at` verbatim when one is given.

    Task.save only stamps completed_at when it is unset, so passing an explicit
    historical time survives — which is what a burndown needs. The status must
    be done alongside it or the CheckConstraint rejects the row, and that
    coupling is the invariant doing its job.
    """
    return Task.objects.create(
        mission=mission,
        title=title,
        weight=weight,
        sprint=sprint,
        assignee=assignee,
        status=TaskStatus.DONE if completed_at else TaskStatus.TODO,
        completed_at=completed_at,
    )


class BurndownShapeTests(TestCase):
    """An 11-day sprint centred on today, so today sits at index 5."""

    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.start = cls.today - timedelta(days=5)
        cls.end = cls.today + timedelta(days=5)

        cls.project, _, cls.mission = _make_project()
        cls.sprint = _make_sprint(
            cls.project, _local(cls.start, 9), _local(cls.end, 17)
        )
        for weight, title in ((1, "a"), (2, "b"), (5, "d")):
            _make_task(cls.mission, weight=weight, sprint=cls.sprint, title=title)
        # Weight 3 finished on day 2. Total scope is 11.
        _make_task(
            cls.mission, weight=3, sprint=cls.sprint, title="c",
            completed_at=_local(cls.start + timedelta(days=2)),
        )

    def burndown(self):
        return SprintMetricsCalculator(self.project).get_burndown()

    def test_labels_cover_every_sprint_day_inclusive(self):
        data = self.burndown()
        self.assertEqual(len(data["labels"]), 11)
        self.assertEqual(data["labels"][0], self.start.strftime("%b %d"))
        self.assertEqual(data["labels"][-1], self.end.strftime("%b %d"))

    def test_all_three_series_are_the_same_length(self):
        data = self.burndown()
        self.assertEqual(len(data["labels"]), len(data["actual"]))
        self.assertEqual(len(data["labels"]), len(data["ideal"]))

    def test_days_after_today_are_none(self):
        actual = self.burndown()["actual"]
        self.assertEqual(actual[6:], [None] * 4 + [None])

    def test_today_is_the_last_plotted_point(self):
        """The whole point: the line stops now instead of running to the edge."""
        actual = self.burndown()["actual"]
        self.assertIsNotNone(actual[5])
        self.assertIsNone(actual[6])

    def test_actual_burns_on_the_day_of_completion(self):
        actual = self.burndown()["actual"]
        self.assertEqual(actual[1], 11)
        self.assertEqual(actual[2], 8)
        self.assertEqual(actual[3], 8)

    def test_ideal_starts_at_opening_scope_and_ends_at_zero(self):
        ideal = self.burndown()["ideal"]
        self.assertEqual(ideal[0], 11.0)
        self.assertEqual(ideal[-1], 0.0)

    def test_ideal_is_evenly_spaced_and_monotonic(self):
        ideal = self.burndown()["ideal"]
        steps = [round(a - b, 2) for a, b in zip(ideal, ideal[1:])]
        for step in steps:
            self.assertAlmostEqual(step, 11 / 10, places=2)
        self.assertEqual(ideal, sorted(ideal, reverse=True))

    def test_ideal_is_drawn_for_days_actual_is_not(self):
        data = self.burndown()
        self.assertTrue(all(value is not None for value in data["ideal"]))
        self.assertIn(None, data["actual"])


class BurndownEdgeCaseTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.project, _, self.mission = _make_project()

    def test_work_finished_before_the_sprint_sets_the_opening_scope(self):
        """Both series start together instead of actual mysteriously starting low."""
        start = self.today - timedelta(days=2)
        sprint = _make_sprint(self.project, _local(start), _local(self.today + timedelta(days=2)))
        _make_task(self.mission, weight=7, sprint=sprint)
        _make_task(
            self.mission, weight=4, sprint=sprint,
            completed_at=_local(start - timedelta(days=3)),
        )

        data = SprintMetricsCalculator(self.project).get_burndown()
        self.assertEqual(data["actual"][0], 7)
        self.assertEqual(data["ideal"][0], 7.0)

    def test_work_finished_after_the_sprint_is_never_burned(self):
        start = self.today - timedelta(days=10)
        end = self.today - timedelta(days=5)
        sprint = _make_sprint(self.project, _local(start), _local(end))
        _make_task(
            self.mission, weight=6, sprint=sprint,
            completed_at=_local(end + timedelta(days=1)),
        )

        data = SprintMetricsCalculator(self.project).get_burndown()
        self.assertEqual(data["actual"][-1], 6)

    def test_sprint_that_has_not_started_has_no_actual_points(self):
        start = self.today + timedelta(days=3)
        sprint = _make_sprint(self.project, _local(start), _local(start + timedelta(days=4)))
        _make_task(self.mission, weight=5, sprint=sprint)

        data = SprintMetricsCalculator(self.project).get_burndown()
        self.assertTrue(all(value is None for value in data["actual"]))
        self.assertEqual(data["ideal"][0], 5.0)

    def test_finished_sprint_has_no_gaps(self):
        start = self.today - timedelta(days=14)
        sprint = _make_sprint(self.project, _local(start), _local(start + timedelta(days=6)))
        _make_task(self.mission, weight=2, sprint=sprint)

        data = SprintMetricsCalculator(self.project).get_burndown()
        self.assertNotIn(None, data["actual"])

    def test_zero_length_sprint_is_one_point(self):
        sprint = _make_sprint(self.project, _local(self.today, 9), _local(self.today, 17))
        _make_task(self.mission, weight=3, sprint=sprint)

        data = SprintMetricsCalculator(self.project).get_burndown()
        self.assertEqual(len(data["labels"]), 1)
        self.assertEqual(data["actual"], [3])
        self.assertEqual(data["ideal"], [0.0])

    def test_end_before_start_is_clamped_to_one_day(self):
        """No DB constraint forbids it, so range(negative) must not silently win."""
        sprint = _make_sprint(
            self.project, _local(self.today), _local(self.today - timedelta(days=1))
        )
        _make_task(self.mission, weight=1, sprint=sprint)

        data = SprintMetricsCalculator(self.project).get_burndown()
        self.assertEqual(len(data["labels"]), 1)

    def test_sprint_with_no_tasks_is_flat_zero(self):
        start = self.today - timedelta(days=2)
        _make_sprint(self.project, _local(start), _local(self.today + timedelta(days=2)))

        data = SprintMetricsCalculator(self.project).get_burndown()
        self.assertEqual(data["actual"][:3], [0, 0, 0])
        self.assertEqual(set(data["ideal"]), {0.0})

    def test_no_sprint_returns_empty_series(self):
        data = SprintMetricsCalculator(self.project).get_burndown()
        self.assertEqual(data, {"labels": [], "actual": [], "ideal": []})

    def test_another_sprints_tasks_are_excluded(self):
        start = self.today - timedelta(days=2)
        a = _make_sprint(self.project, _local(start), _local(self.today), name="A")
        b = _make_sprint(
            self.project, _local(start - timedelta(days=9)),
            _local(start - timedelta(days=5)), name="B",
        )
        _make_task(self.mission, weight=3, sprint=a)
        _make_task(self.mission, weight=99, sprint=b)

        data = SprintMetricsCalculator(self.project).get_burndown(a)
        self.assertEqual(data["actual"][0], 3)

    def test_another_projects_tasks_are_excluded(self):
        start = self.today - timedelta(days=2)
        sprint = _make_sprint(self.project, _local(start), _local(self.today))
        _make_task(self.mission, weight=3, sprint=sprint)

        other_project, _, other_mission = _make_project("Other")
        other_sprint = _make_sprint(other_project, _local(start), _local(self.today))
        _make_task(other_mission, weight=50, sprint=other_sprint)

        data = SprintMetricsCalculator(self.project).get_burndown()
        self.assertEqual(data["actual"][0], 3)


@override_settings(TIME_ZONE="Pacific/Kiritimati")
class BurndownTimezoneTests(TestCase):
    """UTC+14, and no DST — so there are no ambiguous wall-times to trip on.

    At +14 a local morning is the previous day in UTC, which is exactly where
    labelling from ``sprint.start_time.date()`` diverged from bucketing by the
    database's own timezone conversion.
    """

    def setUp(self):
        self.today = timezone.localdate()
        self.project, _, self.mission = _make_project()
        self.start = self.today - timedelta(days=4)
        self.sprint = _make_sprint(
            self.project, _local(self.start, 8), _local(self.today + timedelta(days=2), 17)
        )

    def test_labels_use_local_dates_not_utc(self):
        data = SprintMetricsCalculator(self.project).get_burndown()
        self.assertEqual(data["labels"][0], self.start.strftime("%b %d"))

        # The assertion the old code failed. Read back from the database, where
        # the value comes out UTC-aware — the in-memory object still carries the
        # local zone it was built with, so comparing against that proves nothing.
        self.sprint.refresh_from_db()
        self.assertNotEqual(
            data["labels"][0], self.sprint.start_time.date().strftime("%b %d")
        )

    def test_completion_buckets_by_local_day(self):
        _make_task(self.mission, weight=4, sprint=self.sprint)
        _make_task(
            self.mission, weight=6, sprint=self.sprint,
            completed_at=_local(self.start + timedelta(days=2), 0, 30),
        )

        data = SprintMetricsCalculator(self.project).get_burndown()
        self.assertEqual(data["actual"][1], 10)
        self.assertEqual(data["actual"][2], 4)

    def test_today_boundary_uses_the_local_date(self):
        data = SprintMetricsCalculator(self.project).get_burndown()
        plotted = [value for value in data["actual"] if value is not None]
        self.assertEqual(len(plotted), (self.today - self.start).days + 1)


class VelocityTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.project, _, self.mission = _make_project()
        self.first = _make_sprint(
            self.project,
            _local(self.today - timedelta(days=20)),
            _local(self.today - timedelta(days=10)),
            name="S1",
        )
        self.second = _make_sprint(
            self.project,
            _local(self.today - timedelta(days=9)),
            _local(self.today + timedelta(days=1)),
            name="S2",
        )

    def test_labels_are_oldest_sprint_first(self):
        velocity = SprintMetricsCalculator(self.project).get_velocity()
        self.assertEqual(velocity["labels"], ["S1", "S2"])

    def test_only_weight_completed_inside_the_window_counts(self):
        """A closed sprint's velocity must stop growing after it closes."""
        _make_task(
            self.mission, weight=8, sprint=self.first,
            completed_at=_local(self.today - timedelta(days=2)),
        )
        velocity = SprintMetricsCalculator(self.project).get_velocity()
        self.assertEqual(velocity["data"][0], 0)

    def test_completion_inside_the_window_counts(self):
        _make_task(
            self.mission, weight=8, sprint=self.first,
            completed_at=_local(self.today - timedelta(days=15)),
        )
        velocity = SprintMetricsCalculator(self.project).get_velocity()
        self.assertEqual(velocity["data"][0], 8)

    def test_open_tasks_do_not_count(self):
        _make_task(self.mission, weight=5, sprint=self.first)
        velocity = SprintMetricsCalculator(self.project).get_velocity()
        self.assertEqual(velocity["data"][0], 0)

    def test_every_sprint_gets_an_entry(self):
        velocity = SprintMetricsCalculator(self.project).get_velocity()
        self.assertEqual(len(velocity["labels"]), len(velocity["data"]))
        self.assertEqual(velocity["data"], [0, 0])


class TaskSummaryTests(TestCase):
    def setUp(self):
        self.project, _, self.mission = _make_project()

    def summary(self):
        return SprintMetricsCalculator(self.project).get_summary()

    def test_totals_and_open_are_consistent(self):
        _make_task(self.mission, weight=3)
        _make_task(self.mission, weight=5, completed_at=timezone.now())
        summary = self.summary()
        self.assertEqual(summary["open_tasks"], summary["total_tasks"] - summary["completed_tasks"])
        self.assertEqual(summary["open_weight"], summary["total_weight"] - summary["completed_weight"])

    def test_empty_project_is_all_zeroes(self):
        summary = self.summary()
        self.assertEqual(summary["total_tasks"], 0)
        self.assertEqual(summary["completion_rate"], 0)
        self.assertEqual(summary["weight_completion_rate"], 0)

    def test_weight_rate_differs_from_count_rate(self):
        """Catches counts copy-pasted into the weight branch."""
        _make_task(self.mission, weight=1, completed_at=timezone.now())
        _make_task(self.mission, weight=8)
        summary = self.summary()
        self.assertEqual(summary["completion_rate"], 50.0)
        self.assertEqual(summary["weight_completion_rate"], 11.1)

    def test_headline_totals_count_tasks_with_no_sprint(self):
        """The KPI cards used to sum over sprints, hiding the whole backlog."""
        _make_task(self.mission, weight=4)
        summary = self.summary()
        self.assertEqual(summary["total_tasks"], 1)
        self.assertEqual(summary["sprinted_tasks"], 0)
        self.assertEqual(summary["backlog_tasks"], 1)

    def test_summarize_tasks_matches_the_queryset_summary(self):
        """Keeps the in-memory and queryset helpers from drifting apart again."""
        _make_task(self.mission, weight=2, completed_at=timezone.now())
        _make_task(self.mission, weight=5)
        calculator = SprintMetricsCalculator(self.project)
        in_memory = summarize_tasks(list(Task.objects.all()))
        self.assertEqual(in_memory, calculator.task_summary(Task.objects.all()))


class StatusBreakdownTests(TestCase):
    def setUp(self):
        self.project, _, self.mission = _make_project()

    def test_all_three_statuses_present_even_when_empty(self):
        _make_task(self.mission)
        counts = SprintMetricsCalculator(self.project).get_status_counts()
        self.assertEqual({item["status"] for item in counts}, set(TaskStatus.values))

    def test_counts_and_weights_match(self):
        _make_task(self.mission, weight=1)
        _make_task(self.mission, weight=2)
        Task.objects.create(
            mission=self.mission, title="doing", weight=5, status=TaskStatus.IN_PROGRESS
        )
        _make_task(self.mission, weight=3, completed_at=timezone.now())

        by_status = {
            item["status"]: item
            for item in SprintMetricsCalculator(self.project).get_status_counts()
        }
        self.assertEqual((by_status["todo"]["tasks"], by_status["todo"]["weight"]), (2, 3))
        self.assertEqual((by_status["in_progress"]["tasks"], by_status["in_progress"]["weight"]), (1, 5))
        self.assertEqual((by_status["done"]["tasks"], by_status["done"]["weight"]), (1, 3))

    def test_done_count_equals_completed_at_not_null(self):
        """Ties the breakdown to the invariant every other metric depends on."""
        _make_task(self.mission, completed_at=timezone.now())
        _make_task(self.mission)

        counts = {item["status"]: item["tasks"] for item in
                  SprintMetricsCalculator(self.project).get_status_counts()}
        self.assertEqual(counts["done"], Task.objects.filter(completed_at__isnull=False).count())


class AssigneeAndCampaignTests(TestCase):
    def setUp(self):
        self.project, self.campaign, self.mission = _make_project()
        person = Person.objects.create(display_name="Ada", email="ada@x.com")
        self.practitioner = Practitioner.objects.create(person=person, role="contributor")

    def test_unassigned_tasks_get_their_own_bucket(self):
        _make_task(self.mission, weight=3)
        items = SprintMetricsCalculator(self.project).get_assignee_items()
        self.assertEqual([item["label"] for item in items], ["Unassigned"])
        self.assertEqual(items[0]["total_weight"], 3)

    def test_assignee_totals_sum_to_project_totals(self):
        _make_task(self.mission, weight=3, assignee=self.practitioner)
        _make_task(self.mission, weight=5)
        calculator = SprintMetricsCalculator(self.project)
        items = calculator.get_assignee_items()
        self.assertEqual(
            sum(item["total_weight"] for item in items),
            calculator.get_summary()["total_weight"],
        )

    def test_campaign_with_no_tasks_is_listed_at_zero(self):
        Campaign.objects.create(project=self.project, name="Empty")
        progress = SprintMetricsCalculator(self.project).get_campaign_progress()
        labels = [item["label"] for item in progress]
        self.assertIn("Empty", labels)
        self.assertEqual(progress[labels.index("Empty")]["pct"], 0)

    def test_campaign_progress_uses_weight_not_count(self):
        _make_task(self.mission, weight=1, completed_at=timezone.now())
        _make_task(self.mission, weight=9)
        progress = SprintMetricsCalculator(self.project).get_campaign_progress()
        mine = next(item for item in progress if item["label"] == self.campaign.name)
        self.assertEqual(mine["pct"], 10.0)


class DaysToCompletionTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.project, _, self.mission = _make_project()
        self.sprint = _make_sprint(
            self.project,
            _local(self.today - timedelta(days=10)),
            _local(self.today),
        )

    def test_measures_days_from_sprint_start(self):
        _make_task(
            self.mission, sprint=self.sprint,
            completed_at=_local(self.today - timedelta(days=7)),
        )
        data = SprintMetricsCalculator(self.project).get_days_to_completion()
        self.assertEqual(data["data"], [3])

    def test_is_capped(self):
        for index in range(25):
            _make_task(
                self.mission, sprint=self.sprint, title=f"T{index}",
                completed_at=_local(self.today - timedelta(days=5)),
            )
        data = SprintMetricsCalculator(self.project).get_days_to_completion()
        self.assertEqual(len(data["labels"]), 20)
        self.assertEqual(len(data["data"]), 20)

    def test_open_tasks_are_absent(self):
        _make_task(self.mission, sprint=self.sprint)
        data = SprintMetricsCalculator(self.project).get_days_to_completion()
        self.assertEqual(data["labels"], [])


class MetricsQueryBudgetTests(TestCase):
    """The N+1 guard.

    The count alone is weak — a per-sprint query passes on a one-sprint fixture.
    Asserting the *same* count on a much larger project is what pins it at O(1).
    """

    def setUp(self):
        self.today = timezone.localdate()
        self.project, _, self.mission = _make_project()
        self.sprint = _make_sprint(
            self.project,
            _local(self.today - timedelta(days=5)),
            _local(self.today + timedelta(days=5)),
        )
        _make_task(self.mission, weight=3, sprint=self.sprint)

    def _grow(self):
        for index in range(4):
            sprint = _make_sprint(
                self.project,
                _local(self.today - timedelta(days=40 + index * 10)),
                _local(self.today - timedelta(days=32 + index * 10)),
                name=f"Extra {index}",
            )
            campaign = Campaign.objects.create(project=self.project, name=f"C{index}")
            mission = Mission.objects.create(campaign=campaign, title=f"M{index}")
            person = Person.objects.create(display_name=f"P{index}", email=f"p{index}@x.com")
            practitioner = Practitioner.objects.create(person=person, role="contributor")
            for task_index in range(10):
                _make_task(
                    mission, weight=2, sprint=sprint, assignee=practitioner,
                    title=f"T{index}-{task_index}",
                )

    def test_context_data_runs_a_constant_number_of_queries(self):
        with self.assertNumQueries(3):  # rows, sprints, campaigns
            SprintMetricsCalculator(self.project).get_context_data()

    def test_query_count_does_not_grow_with_data(self):
        self._grow()
        with self.assertNumQueries(3):
            SprintMetricsCalculator(self.project).get_context_data()

    def test_burndown_alone_touches_only_sprints_and_rows(self):
        calculator = SprintMetricsCalculator(self.project)
        with self.assertNumQueries(2):
            calculator.get_burndown()
        with self.assertNumQueries(0):  # cached_property: the second call is free
            calculator.get_burndown()


CHART_KEYS = [
    "sprint_completion_chart_json",
    "sprint_task_chart_json",
    "burndown_chart_json",
    "velocity_chart_json",
    "lead_time_chart_json",
    "assignee_workload_chart_json",
    "status_chart_json",
]


class SprintMetricsViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # PageProcessor 404s without an active Platform. No theme, or the chart
        # partial serializes one and the query count moves.
        Platform.objects.create(
            site_name="Test", author="Test", publication_year=2026, active=True
        )
        cls.user = User.objects.create_user(username="metricsuser", password="pass")
        cls.today = timezone.localdate()
        cls.project, _, cls.mission = _make_project()
        cls.older = _make_sprint(
            cls.project,
            _local(cls.today - timedelta(days=20)),
            _local(cls.today - timedelta(days=12)),
            name="Older",
        )
        cls.current = _make_sprint(
            cls.project,
            _local(cls.today - timedelta(days=3)),
            _local(cls.today + timedelta(days=5)),
            name="Current",
        )
        _make_task(cls.mission, weight=5, sprint=cls.current)
        _make_task(
            cls.mission, weight=2, sprint=cls.current,
            completed_at=_local(cls.today - timedelta(days=1)),
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.url = f"/kanban/projects/{self.project.pk}/sprint-metrics/"

    def test_requires_login(self):
        self.client.logout()
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 302)

    def test_returns_200(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertIn("kanban/sprint_metrics.html", [t.name for t in res.templates])

    def test_chart_canvases_are_inside_bounded_containers(self):
        res = self.client.get(self.url)
        chart_ids = [
            "sprintCompletionChart",
            "sprintTaskChart",
            "burndownChart",
            "statusChart",
            "velocityChart",
            "leadTimeChart",
            "assigneeWorkloadChart",
        ]
        html = res.content.decode()
        for chart_id in chart_ids:
            with self.subTest(chart_id=chart_id):
                self.assertRegex(
                    html,
                    re.compile(
                        rf'<div class="h-72">\s*'
                        rf'<div class="chart-container"[^>]*>\s*'
                        rf'<canvas id="{chart_id}">',
                        re.MULTILINE,
                    ),
                )

    def test_every_chart_key_is_present_and_parses(self):
        res = self.client.get(self.url)
        for key in CHART_KEYS:
            with self.subTest(chart=key):
                self.assertIn(key, res.context)
                chart = json.loads(res.context[key])
                self.assertTrue({"chart_type", "labels", "datasets"} <= set(chart))
                self.assertEqual(len(chart["labels"]), len(chart["datasets"][0]["data"]))

    def test_burndown_chart_has_actual_and_ideal_datasets(self):
        chart = json.loads(self.client.get(self.url).context["burndown_chart_json"])
        self.assertEqual(len(chart["datasets"]), 2)
        self.assertEqual(
            [dataset["label"] for dataset in chart["datasets"]],
            ["Remaining weight", "Ideal"],
        )
        for dataset in chart["datasets"]:
            self.assertEqual(len(dataset["data"]), len(chart["labels"]))

    def test_burndown_actual_has_a_null_tail_and_ideal_does_not(self):
        """Proves None survives json.dumps, the context and the template."""
        chart = json.loads(self.client.get(self.url).context["burndown_chart_json"])
        actual, ideal = chart["datasets"][0]["data"], chart["datasets"][1]["data"]
        self.assertIn(None, actual)
        self.assertNotIn(None, ideal)

    def test_burndown_dataset_does_not_span_gaps(self):
        chart = json.loads(self.client.get(self.url).context["burndown_chart_json"])
        self.assertIs(chart["datasets"][0]["spanGaps"], False)

    def test_selected_sprint_from_query_string(self):
        res = self.client.get(self.url, {"sprint": self.older.pk})
        self.assertEqual(res.context["selected_sprint"], self.older)
        chart = json.loads(res.context["burndown_chart_json"])
        expected = timezone.localtime(self.older.start_time).date().strftime("%b %d")
        self.assertEqual(chart["labels"][0], expected)

    def test_unknown_sprint_id_falls_back_to_latest(self):
        res = self.client.get(self.url, {"sprint": 999999})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["selected_sprint"], self.current)

    def test_project_without_sprints_renders_empty_charts(self):
        other, _, _ = _make_project("Sprintless")
        res = self.client.get(f"/kanban/projects/{other.pk}/sprint-metrics/")
        self.assertEqual(res.status_code, 200)
        chart = json.loads(res.context["burndown_chart_json"])
        self.assertEqual(chart["labels"], [])

    def test_view_makes_one_task_query(self):
        """Filtered to kanban_task so host plugins and themes cannot perturb it."""
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(self.url)
        task_queries = [q for q in ctx.captured_queries if "kanban_task" in q["sql"]]
        self.assertEqual(
            len(task_queries), 1, "\n\n".join(q["sql"] for q in task_queries)
        )
