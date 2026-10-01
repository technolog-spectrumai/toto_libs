"""The nightly housekeeping (2026-10-01, RODO; ``toto.core.housekeeping``):
Django's clearsessions, the sign-in rows of sessions that are gone, the lapsed
membership applications (their rules are socialhub's
``tests_application_housekeeping``), ONE audit record a run with counts only
— and the beat entry that runs it every night.

    manage.py test toto.core.tests_housekeeping
"""

import json
from datetime import timedelta
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from toto.core import housekeeping, user_sessions
from toto.core.models import UserSession

User = get_user_model()


def session(*, expired=False) -> str:
    store = SessionStore()
    store["seen"] = True
    store.create()
    if expired:
        Session.objects.filter(session_key=store.session_key).update(
            expire_date=timezone.now() - timedelta(days=1))
    return store.session_key


class SessionsCase(TestCase):
    def setUp(self):
        cache.clear()       # last-seen markers live there, not in the rolled-back database
        self.ada = User.objects.create_user("ada", "ada@example.com", "pw")

    def row(self, key):
        return UserSession.objects.create(user=self.ada, session_key=key)


class ClearSessionsTests(SessionsCase):
    def test_expired_sessions_leave_the_store(self):
        live = session()
        session(expired=True)
        self.assertEqual(housekeeping.clear_sessions(), 1)
        self.assertEqual(set(Session.objects.values_list("session_key", flat=True)), {live})

    def test_it_is_djangos_clearsessions(self):
        with mock.patch("django.core.management.call_command") as call:
            housekeeping.clear_sessions()
        self.assertEqual(call.call_args.args, ("clearsessions",))


class SessionRowTests(SessionsCase):
    def test_rows_whose_session_is_gone_are_dropped(self):
        live = session()
        self.row(live)
        self.row(session(expired=True))       # expired, not yet cleared
        self.row("gone" * 8)                  # cleared, or ended elsewhere
        self.assertEqual(user_sessions.prune_dead(), 2)
        self.assertEqual(list(UserSession.objects.values_list("session_key", flat=True)), [live])

    def test_it_walks_the_whole_table_in_batches(self):
        for n in range(5):
            self.row(f"gone{n:02d}" + "x" * 26)
        keep = session()
        self.row(keep)
        self.assertEqual(user_sessions.prune_dead(batch=2), 5)
        self.assertEqual(list(UserSession.objects.values_list("session_key", flat=True)), [keep])

    @override_settings(SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies")
    def test_a_store_with_nothing_to_ask_keeps_its_rows(self):
        self.row("cookie" * 6)
        self.assertEqual(user_sessions.prune_dead(), 0)
        self.assertEqual(UserSession.objects.count(), 1)


class RunTests(SessionsCase):
    def lapsed_application(self):
        from toto.socialhub.models import Community, MembershipApplication

        User.objects.create(username="lapsed", email="lapsed@example.com", is_active=False)
        return MembershipApplication.objects.create(
            email="lapsed@example.com", community=Community.objects.create(name="C", slug="c"),
            code="161616", expires_at=timezone.now() - timedelta(days=45))

    def records(self):
        from toto.audit.models import AuditRecord

        return AuditRecord.objects.filter(action="PRIVACY.HOUSEKEEPING")

    def test_one_night_does_every_step_and_records_counts_only(self):
        if not apps.is_installed("toto.audit"):
            self.skipTest("toto.audit is not installed")
        dead = session(expired=True)
        self.row(dead)
        self.row("gone" * 8)
        socialhub = apps.is_installed("toto.socialhub")
        if socialhub:
            self.lapsed_application()

        result = housekeeping.run()

        self.assertEqual((result["sessions_cleared"], result["session_rows_dropped"],
                          result["failed"]), (1, 2, {}))
        if socialhub:
            self.assertEqual((result["applications_pruned"], result["accounts_deleted"],
                              result["applications_kept"], result["expired_days"]),
                             (1, 1, 0, 30))
        record = self.records().get()
        self.assertIsNone(record.actor_user_id)
        self.assertTrue(record.success)
        self.assertEqual((record.app_label, record.source), ("core", "beat"))
        self.assertEqual(record.metadata["session_rows_dropped"], 2)
        # Who was pruned stays pruned: no address, name or key on the chain.
        written = json.dumps(record.metadata) + record.object_description + record.object_id
        for kept_out in ("lapsed", "example.com", dead, "gone"):
            self.assertNotIn(kept_out, written)

    def test_a_failing_step_is_named_and_the_others_still_run(self):
        session(expired=True)
        with mock.patch("toto.core.user_sessions.prune_dead", side_effect=RuntimeError("boom")):
            result = housekeeping.run()
        self.assertEqual(result["failed"], {"session_rows": "RuntimeError"})
        self.assertIsNone(result["session_rows_dropped"])
        self.assertEqual(result["sessions_cleared"], 1)
        if apps.is_installed("toto.audit"):
            self.assertFalse(self.records().get().success)

    def test_a_second_run_finds_nothing_due(self):
        session(expired=True)
        housekeeping.run()
        result = housekeeping.run()
        self.assertEqual((result["sessions_cleared"], result["session_rows_dropped"]), (0, 0))
        if apps.is_installed("toto.audit"):
            self.assertEqual(self.records().count(), 2)

    def test_the_task_runs_it(self):
        from toto.core.tasks import nightly_housekeeping

        session(expired=True)
        self.assertEqual(nightly_housekeeping()["sessions_cleared"], 1)


class BeatEntryTests(SimpleTestCase):
    def test_the_nightly_housekeeping_is_scheduled_when_enabled(self):
        from toto.schedules import beat_schedule

        entry = beat_schedule(housekeeping=True)["core-nightly-housekeeping"]
        self.assertEqual(entry["task"], "toto.core.tasks.nightly_housekeeping")
        self.assertEqual((entry["schedule"].hour, entry["schedule"].minute), ({3}, {5}))
        self.assertNotIn("core-nightly-housekeeping", beat_schedule())

    def test_the_worker_can_find_it(self):
        from toto.core import tasks
        from toto.registry import TASK_MODULES

        self.assertIn("toto.core", TASK_MODULES)
        self.assertEqual(tasks.nightly_housekeeping.name, tasks.TASK_NAME)

    def test_the_host_schedules_it_by_default(self):
        from django.conf import settings

        # A host that does not declare the switch (or turned it off) is not
        # this test's business.
        if getattr(settings, "NIGHTLY_HOUSEKEEPING", False) is not True:
            self.skipTest("the host does not switch the nightly housekeeping on")
        self.assertEqual(settings.CELERY_BEAT_SCHEDULE["core-nightly-housekeeping"]["task"],
                         "toto.core.tasks.nightly_housekeeping")
