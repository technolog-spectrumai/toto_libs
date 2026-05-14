from django.test import TestCase
from django.contrib.auth import get_user_model

from toto.core.models import Platform
from toto.noosphere.models import SyncRule, SyncRun, SyncObjectRun


def _platform():
    return Platform.objects.first() or Platform.objects.create(
        site_name="Test Platform",
        author="test",
        publication_year=2024,
    )


class SyncRuleSmoke(TestCase):
    def setUp(self):
        self.platform = _platform()

    def test_create_rule(self):
        rule = SyncRule.objects.create(
            platform=self.platform,
            name="events-up",
            model_label="events.Event",
            direction=SyncRule.DIRECTION_UP,
        )
        self.assertEqual(rule.app_label, "events")
        self.assertEqual(rule.model_name, "Event")

    def test_unique_constraint(self):
        SyncRule.objects.create(
            platform=self.platform,
            name="rule-a",
            model_label="events.Event",
            direction=SyncRule.DIRECTION_UP,
        )
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            SyncRule.objects.create(
                platform=self.platform,
                name="rule-b",
                model_label="events.Event",
                direction=SyncRule.DIRECTION_UP,
            )

    def test_mark_pushed(self):
        rule = SyncRule.objects.create(
            platform=self.platform,
            name="kanban-up",
            model_label="kanban.Task",
            direction=SyncRule.DIRECTION_UP,
        )
        self.assertIsNone(rule.last_pushed_at)
        rule.mark_pushed()
        rule.refresh_from_db()
        self.assertIsNotNone(rule.last_pushed_at)


class SyncRunSmoke(TestCase):
    def setUp(self):
        self.platform = _platform()
        self.rule = SyncRule.objects.create(
            platform=self.platform,
            name="events-down",
            model_label="events.Event",
            direction=SyncRule.DIRECTION_DOWN,
        )

    def test_create_run_and_finish(self):
        run = SyncRun.objects.create(
            platform=self.platform,
            rule=self.rule,
            direction=SyncRule.DIRECTION_DOWN,
        )
        self.assertEqual(run.status, SyncRun.STATUS_RUNNING)
        run.mark_success()
        run.refresh_from_db()
        self.assertEqual(run.status, SyncRun.STATUS_SUCCESS)
        self.assertIsNotNone(run.finished_at)

    def test_run_survives_rule_deletion(self):
        run = SyncRun.objects.create(
            platform=self.platform,
            rule=self.rule,
            direction=SyncRule.DIRECTION_DOWN,
        )
        self.rule.delete()
        run.refresh_from_db()
        self.assertIsNone(run.rule)


class SyncObjectRunSmoke(TestCase):
    def setUp(self):
        platform = _platform()
        rule = SyncRule.objects.create(
            platform=platform,
            name="locs-up",
            model_label="locations.Point",
            direction=SyncRule.DIRECTION_UP,
        )
        self.run = SyncRun.objects.create(
            platform=platform,
            rule=rule,
            direction=SyncRule.DIRECTION_UP,
        )

    def test_create_object_run(self):
        obj = SyncObjectRun.objects.create(
            run=self.run,
            model_label="locations.Point",
            uid="abc-123",
            action=SyncObjectRun.ACTION_CREATE,
            status=SyncObjectRun.STATUS_SUCCESS,
        )
        self.assertEqual(str(obj), "locations.Point uid=abc-123 — create — success")
