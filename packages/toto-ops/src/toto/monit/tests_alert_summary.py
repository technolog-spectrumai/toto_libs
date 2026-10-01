"""The Alerts section of the Database page (2026-10-01): who the scheduled
checks mail — masked — how often they run, what each check last mailed and
how the last alert mail fared. ``toto.monit.alerts.overview`` directly, and
the page through the full middleware stack."""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

from celery.schedules import crontab
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import NoticeDelivery, Platform
from toto.monit import alerts
from toto.monit.models import CheckState

User = get_user_model()

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
CONSOLE = "django.core.mail.backends.console.EmailBackend"
SCHEDULE = {"monit-alert-checks": {"task": alerts.TASK,
                                   "schedule": crontab(minute="*/5")}}


@override_settings(ALERT_EMAILS=["ops@example.test", "oncall@example.test"],
                   ADMINS=[("dev@example.test", "dev@example.test")],
                   ALERT_REMIND_HOURS=6, CELERY_BEAT_SCHEDULE=SCHEDULE,
                   EMAIL_BACKEND=LOCMEM)
class OverviewTests(TestCase):
    def test_who_is_mailed_how_often_and_never_an_address_in_full(self):
        summary = alerts.overview()
        self.assertEqual(summary["recipients"], ["o***@example.test", "o***@example.test"])
        self.assertEqual(summary["error_recipients"], ["d***@example.test"])
        self.assertTrue(summary["scheduled"])
        self.assertEqual(summary["every"], "5\xa0minutes")
        self.assertEqual(summary["remind_hours"], 6)
        self.assertTrue(summary["delivers"])
        self.assertEqual((summary["last_run"], summary["states"], summary["deliveries"]),
                         (None, [], []))

    @override_settings(CELERY_BEAT_SCHEDULE={})
    def test_off_the_beat_is_said(self):
        summary = alerts.overview()
        self.assertEqual((summary["scheduled"], summary["every"]), (False, ""))

    @override_settings(ADMINS=["dev@example.test", ("Dev", "dev@example.test"),
                               ("Ops", "ops@example.test"), ("", "")])
    def test_crash_recipients_come_as_pairs_or_bare_and_once_each(self):
        self.assertEqual(alerts.error_recipients(), ["dev@example.test", "ops@example.test"])

    def test_the_kept_states_and_the_last_mail_of_each_kind(self):
        now = timezone.now()
        CheckState.objects.create(key="disk", label="Disk", status="ok",
                                  checked_at=now - timedelta(minutes=9))
        CheckState.objects.create(key="backups", label="Backups", status="fail",
                                  checked_at=now - timedelta(minutes=4),
                                  alerted_status="fail", alerted_at=now)
        NoticeDelivery.objects.create(purpose="check_recovered", status="sent")
        NoticeDelivery.objects.create(purpose="check_alert", status="failed", tries=5)
        NoticeDelivery.objects.create(purpose="new_sign_in", status="failed")
        summary = alerts.overview()
        self.assertEqual([state.key for state in summary["states"]], ["backups", "disk"])
        self.assertEqual(summary["last_run"], now - timedelta(minutes=4))
        self.assertEqual([row.purpose for row in summary["deliveries"]],
                         ["check_alert", "check_recovered"])

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_a_backend_that_reaches_nobody_is_named(self):
        summary = alerts.overview()
        self.assertEqual((summary["delivers"], summary["backend"]), (False, "console"))


@override_settings(ALERT_EMAILS=["ops@example.test"],
                   ADMINS=[("dev@example.test", "dev@example.test")],
                   ALERT_REMIND_HOURS=6, CELERY_BEAT_SCHEDULE=SCHEDULE,
                   EMAIL_BACKEND=CONSOLE)
class AlertSectionPageTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="T", author="T", publication_year=2026,
                                active=True)
        self.client.force_login(User.objects.create_superuser("root", "r@x.test", "pw"))
        now = timezone.now()
        CheckState.objects.create(key="backups", label="Backups", status="fail",
                                  since=now - timedelta(hours=8),
                                  bad_since=now - timedelta(hours=8),
                                  checked_at=now - timedelta(minutes=3),
                                  alerted_status="fail",
                                  alerted_at=now - timedelta(hours=2))
        CheckState.objects.create(key="disk", label="Disk", status="ok",
                                  checked_at=now - timedelta(minutes=3))
        NoticeDelivery.objects.create(purpose="check_alert", status="failed", tries=5,
                                      error="SMTPAuthenticationError (535)", failures=1)

    def page(self):
        response = self.client.get(reverse("monit:status"))
        self.assertEqual(response.status_code, 200)
        return response

    def test_the_page_says_who_is_mailed_and_never_in_full(self):
        response = self.page()
        self.assertContains(response, 'data-testid="alerts"')
        self.assertContains(response, "o***@example.test")
        self.assertContains(response, "d***@example.test")
        self.assertNotContains(response, "ops@example.test")
        self.assertNotContains(response, "dev@example.test")
        self.assertContains(response, "Every 5\xa0minutes, in the worker.")
        self.assertContains(response, "A failing check is mailed again every 6 hours.")

    def test_each_check_its_last_alert_and_the_last_mail(self):
        response = self.page()
        self.assertContains(response, 'data-testid="alert-table"')
        self.assertContains(response, 'data-testid="alert-cards"')
        self.assertContains(response, 'data-testid="alert-row-backups"')
        self.assertContains(response, 'data-testid="alert-row-disk"')
        self.assertContains(response, "Nothing open")
        self.assertContains(response, "SMTPAuthenticationError (535)")
        self.assertContains(response, "try 5")
        self.assertContains(response, "the mail backend is console")
        self.assertContains(response, "cannot report the server itself being down")

    @override_settings(ALERT_EMAILS=[], ADMINS=[], CELERY_BEAT_SCHEDULE={},
                       EMAIL_BACKEND=LOCMEM)
    def test_nobody_and_no_schedule_are_said_out_loud(self):
        CheckState.objects.all().delete()
        NoticeDelivery.objects.all().delete()
        response = self.page()
        self.assertContains(response, "Nobody: no address is set (ALERT_EMAILS), so nothing is mailed.")
        self.assertContains(response, "Nobody: no address is set (ERROR_EMAILS, else ALERT_EMAILS).")
        self.assertContains(response, "Not on a schedule here, so nothing is mailed")
        self.assertContains(response, "None sent yet.")
        self.assertNotContains(response, 'data-testid="alert-table"')
        self.assertNotContains(response, "the mail backend is")

    def test_the_page_renders_without_the_summary(self):
        with mock.patch.object(alerts, "overview", side_effect=RuntimeError("boom")):
            response = self.page()
        self.assertNotContains(response, 'data-testid="alerts"')
        self.assertContains(response, "Every check, measured just now")
