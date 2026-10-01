"""The record checks: intact, migrated, backed up — and never a 500.

Ported with the feature from the placidia truth book's ops app.
"""

from __future__ import annotations

import io
import tempfile
import time
from pathlib import Path

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.monit import record

User = get_user_model()


class CheckTests(TestCase):
    def test_the_database_and_migrations_pass_on_a_test_run(self):
        self.assertEqual(record.check_database().status, record.OK)
        self.assertEqual(record.check_migrations().status, record.OK)

    def test_a_broken_probe_reports_unknown_never_raises(self):
        """The one rule. A status page that 500s because one probe threw is
        unavailable exactly when it is needed."""
        @record._guard("boom", "Boom")
        def exploding():
            raise RuntimeError("the probe is on fire")

        check = exploding()
        self.assertEqual(check.status, record.UNKNOWN)
        self.assertIn("RuntimeError", check.detail)

    def test_media_store_reports_missing_and_writable(self):
        with tempfile.TemporaryDirectory() as scratch:
            with override_settings(MEDIA_ROOT=str(Path(scratch) / "gone")):
                self.assertEqual(record.check_media().status, record.FAIL)
            with override_settings(MEDIA_ROOT=scratch):
                self.assertEqual(record.check_media().status, record.OK)

    def test_backups_are_off_when_nothing_is_declared(self):
        with override_settings(MONIT_BACKUP_DIRS=[]):
            self.assertEqual(record.check_backups().status, record.OFF)

    def test_backup_freshness_not_existence(self):
        """A directory full of last year is worse than an empty one."""
        with tempfile.TemporaryDirectory() as scratch:
            with override_settings(MONIT_BACKUP_DIRS=[scratch]):
                self.assertEqual(record.check_backups().status, record.WARN)

                stale = Path(scratch) / "dump-old.sql.gz"
                stale.write_bytes(b"x")
                old = time.time() - (record.BACKUP_STALE_HOURS + 5) * 3600
                import os
                os.utime(stale, (old, old))
                self.assertEqual(record.check_backups().status, record.FAIL)

                fresh = Path(scratch) / "dump-new.sql.gz"
                fresh.write_bytes(b"x")
                self.assertEqual(record.check_backups().status, record.OK)

    def test_the_audit_chain_is_checked_where_installed(self):
        from django.apps import apps as django_apps

        check = record.check_audit()
        if django_apps.is_installed("toto.audit"):
            self.assertEqual(check.status, record.OK)
        else:
            self.assertEqual(check.status, record.OFF)

    def test_worst_ranks_fail_over_warn_over_unknown(self):
        mk = lambda status: record.Check("k", "K", status, "s")
        self.assertEqual(record.worst([mk(record.OK)]), record.OK)
        self.assertEqual(record.worst([mk(record.OK), mk(record.WARN)]),
                         record.WARN)
        self.assertEqual(record.worst([mk(record.WARN), mk(record.FAIL)]),
                         record.FAIL)


class StatusPageTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test", author="Tests",
                                publication_year=2026, active=True)

    def test_the_page_is_for_operators_only(self):
        url = reverse("monit:status")
        # Anonymous: 403 from the view's own gate on a plain host, 302 on a
        # host whose global login middleware turns strangers away earlier.
        self.assertIn(self.client.get(url).status_code, (302, 403))
        self.client.force_login(User.objects.create_user("m", password="x"))
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_the_verdict_leads_the_page(self):
        root = User.objects.create_superuser("root", "r@x.com", "x")
        # The page needs the Superuser plan too (2026-10-01); bootstrap_plans
        # puts every superuser on it.
        if django_apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=io.StringIO())
        self.client.force_login(User.objects.get(pk=root.pk))
        with tempfile.TemporaryDirectory() as scratch, \
             override_settings(MEDIA_ROOT=scratch, MONIT_BACKUP_DIRS=[]):
            response = self.client.get(reverse("monit:status"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Migrations")
        self.assertContains(response, "Audit chain")
