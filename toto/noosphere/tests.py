from django.test import TestCase

from toto.core.models import Platform
from toto.noosphere.models import RemotePlatform, SyncRule, SyncRun, SyncObjectRun


def _platform():
    return Platform.objects.first() or Platform.objects.create(
        site_name="Test Platform",
        author="test",
        publication_year=2024,
    )


def _remote(local_platform, name="Studio", base_url="https://studio.example.com"):
    return RemotePlatform.objects.create(
        local_platform=local_platform,
        name=name,
        base_url=base_url,
    )


class RemotePlatformSmoke(TestCase):
    def setUp(self):
        self.platform = _platform()

    def test_create(self):
        rp = _remote(self.platform)
        self.assertEqual(str(rp), f"Studio — https://studio.example.com")
        self.assertEqual(rp.normalized_base_url, "https://studio.example.com")

    def test_backend_key_for_direction(self):
        rp = RemotePlatform.objects.create(
            local_platform=self.platform,
            name="Portal",
            base_url="https://portal.example.com",
            uplink_backend="studio_https",
            downlink_backend="studio_tor",
        )
        self.assertEqual(rp.get_backend_key_for_direction("up"), "studio_https")
        self.assertEqual(rp.get_backend_key_for_direction("down"), "studio_tor")
        self.assertEqual(rp.get_backend_key_for_direction("other"), "default")

    def test_unique_name_per_local(self):
        _remote(self.platform, name="Studio", base_url="https://studio.example.com")
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            _remote(self.platform, name="Studio", base_url="https://studio2.example.com")


class SyncRuleSmoke(TestCase):
    def setUp(self):
        self.platform = _platform()
        self.remote = _remote(self.platform)

    def test_create_rule(self):
        rule = SyncRule.objects.create(
            local_platform=self.platform,
            remote_platform=self.remote,
            name="events-up",
            model_label="events.Event",
            direction=SyncRule.DIRECTION_UP,
        )
        self.assertEqual(rule.app_label, "events")
        self.assertEqual(rule.model_name, "Event")
        self.assertEqual(rule.platform, self.platform)

    def test_unique_constraint(self):
        SyncRule.objects.create(
            local_platform=self.platform,
            remote_platform=self.remote,
            name="rule-a",
            model_label="events.Event",
            direction=SyncRule.DIRECTION_UP,
        )
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            SyncRule.objects.create(
                local_platform=self.platform,
                remote_platform=self.remote,
                name="rule-b",
                model_label="events.Event",
                direction=SyncRule.DIRECTION_UP,
            )

    def test_clean_rejects_mismatched_remote(self):
        other_platform = Platform.objects.create(
            site_name="Other Platform",
            author="other",
            publication_year=2024,
        )
        other_remote = _remote(other_platform, name="Other Remote", base_url="https://other.example.com")

        rule = SyncRule(
            local_platform=self.platform,
            remote_platform=other_remote,
            name="bad-rule",
            model_label="events.Event",
            direction=SyncRule.DIRECTION_UP,
        )
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            rule.clean()

    def test_mark_pushed(self):
        rule = SyncRule.objects.create(
            local_platform=self.platform,
            remote_platform=self.remote,
            name="kanban-up",
            model_label="kanban.Task",
            direction=SyncRule.DIRECTION_UP,
        )
        self.assertIsNone(rule.last_pushed_at)
        rule.mark_pushed()
        rule.refresh_from_db()
        self.assertIsNotNone(rule.last_pushed_at)

    def test_mark_pulled(self):
        rule = SyncRule.objects.create(
            local_platform=self.platform,
            remote_platform=self.remote,
            name="kanban-down",
            model_label="kanban.Task",
            direction=SyncRule.DIRECTION_DOWN,
        )
        rule.mark_pulled()
        rule.refresh_from_db()
        self.assertIsNotNone(rule.last_pulled_at)


class SyncRunSmoke(TestCase):
    def setUp(self):
        self.platform = _platform()
        self.remote = _remote(self.platform)
        self.rule = SyncRule.objects.create(
            local_platform=self.platform,
            remote_platform=self.remote,
            name="events-down",
            model_label="events.Event",
            direction=SyncRule.DIRECTION_DOWN,
        )

    def test_create_run_and_finish(self):
        run = SyncRun.objects.create(
            local_platform=self.platform,
            remote_platform=self.remote,
            rule=self.rule,
            direction=SyncRule.DIRECTION_DOWN,
        )
        self.assertEqual(run.status, SyncRun.STATUS_RUNNING)
        self.assertEqual(run.platform, self.platform)

        run.mark_success()
        run.refresh_from_db()
        self.assertEqual(run.status, SyncRun.STATUS_SUCCESS)
        self.assertIsNotNone(run.finished_at)

    def test_mark_partial(self):
        run = SyncRun.objects.create(
            local_platform=self.platform,
            remote_platform=self.remote,
            rule=self.rule,
            direction=SyncRule.DIRECTION_DOWN,
        )
        run.mark_partial()
        run.refresh_from_db()
        self.assertEqual(run.status, SyncRun.STATUS_PARTIAL)

    def test_mark_failed(self):
        run = SyncRun.objects.create(
            local_platform=self.platform,
            remote_platform=self.remote,
            rule=self.rule,
            direction=SyncRule.DIRECTION_DOWN,
        )
        run.mark_failed(message="Connection refused.")
        run.refresh_from_db()
        self.assertEqual(run.status, SyncRun.STATUS_FAILED)
        self.assertEqual(run.message, "Connection refused.")

    def test_run_survives_rule_deletion(self):
        run = SyncRun.objects.create(
            local_platform=self.platform,
            remote_platform=self.remote,
            rule=self.rule,
            direction=SyncRule.DIRECTION_DOWN,
        )
        self.rule.delete()
        run.refresh_from_db()
        self.assertIsNone(run.rule)


class SyncObjectRunSmoke(TestCase):
    def setUp(self):
        platform = _platform()
        remote = _remote(platform)
        rule = SyncRule.objects.create(
            local_platform=platform,
            remote_platform=remote,
            name="locs-up",
            model_label="locations.Point",
            direction=SyncRule.DIRECTION_UP,
        )
        self.run = SyncRun.objects.create(
            local_platform=platform,
            remote_platform=remote,
            rule=rule,
            direction=SyncRule.DIRECTION_UP,
        )

    def test_create_and_str(self):
        obj = SyncObjectRun.objects.create(
            run=self.run,
            model_label="locations.Point",
            uid="abc-123",
            action=SyncObjectRun.ACTION_CREATE,
            status=SyncObjectRun.STATUS_SUCCESS,
        )
        self.assertEqual(str(obj), "locations.Point uid=abc-123 — create — success")

    def test_object_runs_accessor(self):
        SyncObjectRun.objects.create(
            run=self.run,
            model_label="locations.Point",
            uid="x-1",
            action=SyncObjectRun.ACTION_UPDATE,
            status=SyncObjectRun.STATUS_SUCCESS,
        )
        self.assertEqual(self.run.object_runs.count(), 1)
