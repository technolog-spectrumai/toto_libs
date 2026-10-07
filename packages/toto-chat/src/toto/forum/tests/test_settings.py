"""The Settings page: who opens it, what it saves, and a cleanup by hand
(stage 69).

For an administrator of the platform only: a real superuser on the
Superuser plan. Never staff alone; not a community's head.

    manage.py test toto.forum.tests.test_settings
"""

import json
import re
from datetime import timedelta
from unittest import mock

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from toto.forum import channels, cleanup, urls, views
from toto.forum.models import (ForumChannel, ForumCleanupRun, ForumMessage, ForumSettings,
                               RunStatus, TriggeredBy)
from toto.forum.testing import client_of, member, on_plan

from .test_cleanup import CleanupCase, no_worker, worker

PAGE = "/forum/settings/"
SAVE = "/forum/settings/save/"
CLEAN = "/forum/settings/cleanup/"
VALID = {"retention_enabled": "on", "retention_days": "90", "refresh_seconds": "10"}
ALL_NOW = {"scope": "all", "age": "everything", "confirm": "yes"}


class SettingsCase(CleanupCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # A superuser who is not on the Superuser plan: Django's flag alone.
        cls.root, _person = member("root", is_superuser=True, is_staff=True)
        on_plan(cls.root)

    def dials(self):
        row = ForumSettings.current()
        return row.retention_enabled, row.retention_days, row.refresh_seconds

    def said(self, response) -> str:
        """The sentences the page shows after a redirect."""
        return " ".join(str(m) for m in response.context["messages"])


class AddressTests(SettingsCase):
    def test_the_three_doors(self):
        self.assertEqual(reverse("forum:settings"), PAGE)
        self.assertEqual(reverse("forum:settings_save"), SAVE)
        self.assertEqual(reverse("forum:cleanup_start"), CLEAN)
        marks = {pattern.name: pattern.callback.forum_door for pattern in urls.urlpatterns}
        for name in ("settings", "settings_save", "cleanup_start"):
            self.assertEqual(marks[name], views.ADMINISTRATOR)

    def test_the_page_is_not_taken_for_a_community(self):
        """`settings/` is matched before `<slug>/`: an administrator gets
        the page, not "Community not found"."""
        response = client_of(self.admin).get(PAGE)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "forum/settings.html")


class AccessTests(SettingsCase):
    def visitors(self):
        """``(who, user, the status every door answers)``."""
        return [("the head", self.head, 403), ("a member", self.member, 403),
                ("a senior member", self.senior, 403), ("staff alone", self.staff, 403),
                ("a superuser without the plan", self.root, 403),
                ("an outsider", self.outsider, 403), ("on Free", self.free, 402)]

    def test_an_administrator_opens_it(self):
        response = client_of(self.admin).get(PAGE)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertContains(response, 'data-testid="forum-settings"')

    def test_nobody_else_opens_it(self):
        for who, user, status in self.visitors():
            with self.subTest(who=who):
                response = client_of(user).get(PAGE)
                self.assertEqual(response.status_code, status)
                self.assertNotContains(response, 'data-testid="forum-settings-form"',
                                       status_code=status)

    def test_signed_out_is_sent_to_sign_in(self):
        for method, url in (("get", PAGE), ("post", SAVE), ("post", CLEAN)):
            with self.subTest(url=url):
                response = getattr(Client(), method)(url)
                self.assertEqual(response.status_code, 302)
                self.assertNotIn("/forum/settings/", response["Location"].split("?")[0])

    def test_nobody_else_saves(self):
        before = self.dials()
        for who, user, status in self.visitors():
            with self.subTest(who=who):
                self.assertEqual(client_of(user).post(SAVE, VALID).status_code, status)
        self.assertEqual(self.dials(), before)

    def test_nobody_else_starts_a_cleanup(self):
        self.message("kept")
        for who, user, status in self.visitors():
            with self.subTest(who=who), worker() as delay:
                self.assertEqual(client_of(user).post(CLEAN, ALL_NOW).status_code, status)
                delay.assert_not_called()
        self.assertFalse(ForumCleanupRun.objects.exists())
        self.assertEqual(ForumMessage.objects.count(), 1)

    def test_the_doors_take_their_method_only(self):
        client = client_of(self.admin)
        self.assertEqual(client.post(PAGE).status_code, 405)
        self.assertEqual(client.get(SAVE).status_code, 405)
        self.assertEqual(client.get(CLEAN).status_code, 405)

    def test_a_write_from_another_site_is_refused(self):
        client = client_of(self.admin)
        before = self.dials()
        self.assertEqual(client.post(SAVE, VALID, HTTP_SEC_FETCH_SITE="cross-site").status_code,
                         403)
        with worker() as delay:
            self.assertEqual(client.post(CLEAN, ALL_NOW,
                                         HTTP_SEC_FETCH_SITE="cross-site").status_code, 403)
        delay.assert_not_called()
        self.assertEqual(self.dials(), before)

    def test_a_form_without_its_token_is_refused(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        before = self.dials()
        self.assertEqual(client.post(SAVE, VALID).status_code, 403)
        self.assertEqual(client.post(CLEAN, ALL_NOW).status_code, 403)
        self.assertEqual(self.dials(), before)
        self.assertFalse(ForumCleanupRun.objects.exists())

    def test_only_an_administrator_is_told_where_the_page_is(self):
        for user, url in ((self.admin, PAGE), (self.head, None), (self.member, None)):
            with self.subTest(user=user.username):
                self.assertEqual(client_of(user).get("/forum/").context["forum_settings_url"], url)
                self.assertEqual(client_of(user).get(self.url("channel_detail"))
                                 .context["forum_settings_url"], url)


class SaveTests(SettingsCase):
    def test_the_dials_arrive_off_and_at_their_defaults(self):
        self.assertEqual(self.dials(), (False, 365, 5))

    def test_valid_values_are_saved_and_the_page_says_so(self):
        response = client_of(self.admin).post(SAVE, VALID, follow=True)
        self.assertRedirects(response, PAGE)
        self.assertEqual(self.dials(), (True, 90, 10))
        self.assertEqual(ForumSettings.current().updated_by, self.admin)
        self.assertIn("saved", self.said(response))
        self.assertEqual(response.context["settings_row"].retention_days, 90)
        self.assertContains(response, 'value="90"')
        self.assertContains(response, 'name="retention_enabled" class="mt-1" checked')

    def test_the_switch_is_off_when_its_box_is_not_ticked(self):
        client = client_of(self.admin)
        client.post(SAVE, VALID)
        client.post(SAVE, {"retention_days": "90", "refresh_seconds": "10"})
        self.assertEqual(self.dials(), (False, 90, 10))

    def test_the_bounds_are_the_server_s(self):
        client = client_of(self.admin)
        for field, good in (("retention_days", ("1", "3650")), ("refresh_seconds", ("2", "120"))):
            for value in good:
                with self.subTest(field=field, value=value):
                    client.post(SAVE, {**VALID, field: value})
                    self.assertEqual(str(getattr(ForumSettings.current(), field)), value)
        client.post(SAVE, VALID)
        before = self.dials()
        for field, bad in (("retention_days", ("0", "3651", "-5", "ten", "", "1.5", "9" * 30)),
                           ("refresh_seconds", ("1", "121", "0", "-2", "soon", "", "2.5"))):
            for value in bad:
                with self.subTest(field=field, value=value):
                    response = client.post(SAVE, {**VALID, field: value}, follow=True)
                    self.assertRedirects(response, PAGE)
                    self.assertIn("Nothing was saved", self.said(response))
                    self.assertEqual(self.dials(), before)

    def test_one_bad_value_saves_none_of_the_three(self):
        client = client_of(self.admin)
        client.post(SAVE, {"retention_days": "45", "refresh_seconds": "999"})
        self.assertEqual(self.dials(), (False, 365, 5))

    def test_the_refresh_interval_reaches_the_channel_s_page(self):
        client_of(self.admin).post(SAVE, {**VALID, "refresh_seconds": "30"})
        html = client_of(self.member).get(self.url("channel_detail")).content.decode()
        block = re.search(r'<script id="forum-channel-config" type="application/json">(.*?)'
                          r"</script>", html, re.S)
        self.assertEqual(json.loads(block.group(1))["refresh_seconds"], 30)


class PageTests(SettingsCase):
    def test_it_shows_what_a_cleanup_would_remove_and_what_the_last_did(self):
        self.message("a year ago", days=400)
        self.message("today")
        self.sweep(days=3000)
        response = client_of(self.admin).get(PAGE)
        self.assertEqual(response.context["preview"]["messages"], 1)
        self.assertEqual(response.context["preview"]["total_messages"], 2)
        self.assertContains(response, 'data-testid="forum-cleanup-preview"')
        self.assertContains(response, 'data-testid="forum-cleanup-run"', count=1)
        self.assertContains(response, '<option value="guild">Guild</option>')
        self.assertFalse(response.context["in_flight"])

    def test_no_schedule_on_this_server_is_said(self):
        row = ForumSettings.current()
        row.retention_enabled = True
        row.save()
        with self.settings(CELERY_BEAT_SCHEDULE={}):
            self.assertContains(client_of(self.admin).get(PAGE),
                                'data-testid="forum-settings-no-schedule"')
        from toto.schedules import beat_schedule

        with self.settings(CELERY_BEAT_SCHEDULE=beat_schedule(forum_cleanup=True)):
            response = client_of(self.admin).get(PAGE)
            self.assertIsNotNone(response.context["next_run"])
            self.assertNotContains(response, 'data-testid="forum-settings-no-schedule"')

    def test_a_community_s_name_is_text(self):
        from toto.socialhub.models import Community

        odd = Community.objects.create(name='<b onmouseover="x()">Odd</b>')
        channels.ensure_channel(odd)
        html = client_of(self.admin).get(PAGE).content.decode()
        self.assertNotIn("<b onmouseover", html)
        self.assertIn("&lt;b onmouseover", html)

    def test_a_running_cleanup_is_said(self):
        cleanup.claim(boundary=timezone.now(), retention_days=0, triggered_by=TriggeredBy.MANUAL)
        self.assertContains(client_of(self.admin).get(PAGE),
                            'data-testid="forum-cleanup-running"')


class ManualCleanupTests(SettingsCase):
    def start(self, **form):
        return client_of(self.admin).post(CLEAN, {**ALL_NOW, **form}, follow=True)

    def run_node(self, run):
        from toto.workflows.models import WorkflowRun
        from toto.workflows.predefined_tasks import run as node

        workflow_run = WorkflowRun.objects.get(pk=run.workflow_run_id)
        with self.captureOnCommitCallbacks(execute=True):
            node("forum_cleanup", workflow_run.input_data)
        run.refresh_from_db()
        return workflow_run

    def test_everything_everywhere(self):
        self.message("one", days=3)
        self.message("two")
        with worker("task-5") as delay:
            response = self.start()
        self.assertRedirects(response, PAGE)
        self.assertIn("was started", self.said(response))
        run = ForumCleanupRun.objects.get()
        self.assertEqual((run.status, run.triggered_by, run.triggered_by_user, run.channel,
                          run.retention_days, run.task_id),
                         (RunStatus.RUNNING, TriggeredBy.MANUAL, self.admin, None, 0, "task-5"))
        self.assertAlmostEqual(run.boundary, timezone.now(), delta=timedelta(minutes=1))
        delay.assert_called_once_with(run.workflow_run_id)
        # Nothing is removed in the request: the worker's node does it.
        self.assertEqual(ForumMessage.objects.count(), 2)
        workflow_run = self.run_node(run)
        self.assertEqual((workflow_run.workflow.slug, workflow_run.started_by),
                         ("forum-cleanup", self.admin))
        self.assertEqual((run.status, run.messages_deleted), (RunStatus.SUCCESS, 2))
        self.assertFalse(ForumMessage.objects.exists())

    def test_one_channel_by_a_chosen_age(self):
        other = channels.ensure_channel(self.other)
        old = self.message("the guild's, old", days=50)
        newer = self.message("the guild's, newer", days=10)
        theirs = self.message("the other's, old", days=50, user=self.outsider,
                              community=self.other)
        with worker():
            self.start(scope="guild", age="days", days="30")
        run = ForumCleanupRun.objects.get()
        self.assertEqual((run.channel, run.channel_name, run.retention_days),
                         (self.channel, "Guild", 30))
        self.assertAlmostEqual(run.boundary, timezone.now() - timedelta(days=30),
                               delta=timedelta(minutes=1))
        self.run_node(run)
        self.assertEqual(set(ForumMessage.objects.values_list("pk", flat=True)),
                         {newer.pk, theirs.pk})
        self.assertFalse(ForumMessage.objects.filter(pk=old.pk).exists())
        self.assertIsNone(ForumChannel.objects.get(pk=other.pk).purged_before)

    def test_by_the_saved_age(self):
        client_of(self.admin).post(SAVE, {**VALID, "retention_days": "20"})
        self.message("old", days=25)
        self.message("newer", days=15)
        with worker():
            self.start(age="retention")
        run = ForumCleanupRun.objects.get()
        self.assertEqual(run.retention_days, 20)
        self.run_node(run)
        self.assertEqual(self.texts(), ["newer"])

    def test_the_boundary_is_never_the_page_s(self):
        """An instant sent with the form is not read: the boundary is the
        server's clock when the cleanup is claimed."""
        self.message("new")
        with worker():
            self.start(age="days", days="30", boundary="2099-01-01T00:00:00Z",
                       retention_days="0")
        run = ForumCleanupRun.objects.get()
        self.assertLess(run.boundary, timezone.now() - timedelta(days=29))
        self.assertEqual(run.retention_days, 30)

    def test_what_is_refused_claims_nothing(self):
        self.message("kept")
        refusals = (
            ({"confirm": ""}, "tick the box"),
            ({"confirm": "no"}, "tick the box"),
            ({"scope": "no-such-community"}, "choose a channel"),
            ({"scope": ""}, "choose a channel"),
            ({"scope": "other"}, "choose a channel"),        # a community with no channel yet
            ({"age": "soon"}, "choose what to remove"),
            ({"age": ""}, "choose what to remove"),
            ({"age": "days", "days": "0"}, "from 1 to 3650"),
            ({"age": "days", "days": "3651"}, "from 1 to 3650"),
            ({"age": "days", "days": ""}, "from 1 to 3650"),
            ({"age": "days", "days": "-3"}, "from 1 to 3650"),
            ({"age": "days", "days": "1.5"}, "from 1 to 3650"),
            ({"age": "days", "days": "many"}, "from 1 to 3650"),
        )
        for form, words in refusals:
            with self.subTest(form=form), worker() as delay:
                response = self.start(**form)
                self.assertRedirects(response, PAGE)
                self.assertIn(words, self.said(response))
                delay.assert_not_called()
        self.assertFalse(ForumCleanupRun.objects.exists())
        self.assertEqual(ForumMessage.objects.count(), 1)

    def test_without_a_worker_nothing_is_claimed_and_the_page_says_so(self):
        self.message("kept")
        with no_worker() as delay:
            response = self.start()
        self.assertIn("No worker is listening", self.said(response))
        delay.assert_not_called()
        self.assertFalse(ForumCleanupRun.objects.exists())
        self.assertEqual(ForumMessage.objects.count(), 1)

    def test_a_second_press_while_one_is_live_is_refused(self):
        with worker() as delay:
            self.start()
            response = self.start()
        self.assertIn("already running", self.said(response))
        delay.assert_called_once()
        self.assertEqual(ForumCleanupRun.objects.count(), 1)

    def test_a_queue_that_refuses_leaves_nothing_live(self):
        with mock.patch("toto.celery_utils.celery_available", return_value=True), \
                mock.patch("toto.workflows.tasks.start_workflow_run_task.delay",
                           side_effect=ConnectionError("no broker")):
            response = self.start()
        self.assertIn("could not be handed to the worker", self.said(response))
        self.assertEqual(ForumCleanupRun.objects.get().status, RunStatus.FAILED)
        self.assertFalse(cleanup.in_flight())
