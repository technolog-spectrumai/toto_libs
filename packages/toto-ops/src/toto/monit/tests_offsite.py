"""The Off-site backup check (2026-10-01): the state files zenobia's restic
container writes after each run, judged like overdue work — no copy for twice
a day plus the grace is WARN, three times FAIL; a failed run is at least
WARN, a repository that failed its check FAIL — and mailed by the alert run
like any other check.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings

from toto.core.models import Platform
from toto.monit import alerts, heartbeats, record

DAY = 86400
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"


class OffsiteCheckTests(TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.dir = Path(scratch.name)
        patcher = override_settings(MONIT_OFFSITE_DIR=str(self.dir))
        patcher.enable()
        self.addCleanup(patcher.disable)

    def state(self, name, **values):
        (self.dir / name).write_text("".join(f"{key}={value}\n"
                                             for key, value in values.items()))

    def copied(self, ago, **values):
        now = time.time()
        self.state("backup", **{"result": "ok", "started": int(now - ago - 60),
                                "finished": int(now - ago), "last_ok": int(now - ago),
                                "snapshot": "1a2b3c4d", "message": "", **values})

    def late(self, times):
        """Seconds a copy is old when it is `times` cadences late, and a minute."""
        return times * DAY + heartbeats.GRACE_SECONDS + 60

    @override_settings(MONIT_OFFSITE_DIR="")
    def test_off_where_the_profile_makes_no_copy(self):
        check = record.check_offsite()
        self.assertEqual((check.key, check.status), ("offsite", record.OFF))
        self.assertEqual(check.summary, "No off-site copy is made on this host.")

    def test_a_fresh_copy_is_fine_and_names_its_snapshot(self):
        self.copied(3 * 3600)
        self.state("check", result="ok", last_ok=int(time.time() - 2 * DAY))
        check = record.check_offsite()
        self.assertEqual(check.status, record.OK)
        self.assertEqual(check.summary, "The newest off-site copy is 3\xa0hours old.")
        self.assertIn("snapshot 1a2b3c4d", check.detail)
        self.assertIn("Last good check:", check.detail)

    def test_a_failed_run_warns_with_restics_answer(self):
        self.copied(3600, result="failed",
                    message="backup failed (restic exit 1): Fatal: unable to open "
                            "repository at s3:https://s3.gra.io.cloud.ovh.net/b/zenobia: "
                            "Access Denied.")
        check = record.check_offsite()
        self.assertEqual(check.status, record.WARN)
        self.assertTrue(check.summary.startswith("The last off-site copy failed: backup "
                                                 "failed (restic exit 1)"), check.summary)
        self.assertIn("Access Denied.", check.summary)

    def test_a_late_copy_is_judged_like_overdue_work(self):
        for times, status in ((1, record.OK), (heartbeats.WARN_AFTER, record.WARN),
                              (heartbeats.FAIL_AFTER, record.FAIL)):
            with self.subTest(times=times):
                self.copied(self.late(times) if times > 1 else DAY)
                self.assertEqual(record.check_offsite().status, status)

    def test_a_copy_that_keeps_failing_ends_failing(self):
        self.copied(self.late(heartbeats.FAIL_AFTER), result="failed",
                    message="not set up: the S3 keys and the passphrase are not on this server")
        check = record.check_offsite()
        self.assertEqual(check.status, record.FAIL)
        self.assertIn("not set up", check.summary)

    def test_a_repository_that_failed_its_check_fails(self):
        self.copied(3600)
        self.state("check", result="failed",
                   message="check failed (restic exit 1): Fatal: repository contains errors")
        check = record.check_offsite()
        self.assertEqual(check.status, record.FAIL)
        self.assertEqual(check.summary, "The off-site repository failed its check: check "
                                        "failed (restic exit 1): Fatal: repository "
                                        "contains errors")
        self.assertIn("No check has passed yet", check.detail)

    def test_before_the_first_copy_it_counts_from_the_containers_start(self):
        self.state("since", since=int(time.time() - 3600))
        check = record.check_offsite()
        self.assertEqual(check.status, record.OK)
        self.assertEqual(check.summary,
                         "No off-site copy yet: the first is made at the next nightly run.")
        self.state("since", since=int(time.time() - self.late(heartbeats.FAIL_AFTER)))
        check = record.check_offsite()
        self.assertEqual(check.status, record.FAIL)
        self.assertTrue(check.summary.startswith("No off-site copy has been made since "))

    def test_nothing_written_yet_counts_from_when_this_host_began_recording(self):
        # On the test database the tables are minutes old: nothing is late.
        self.assertEqual(record.check_offsite().status, record.OK)

    def test_a_running_copy_is_said_and_keeps_the_last_verdict(self):
        self.copied(DAY, result="running")
        check = record.check_offsite()
        self.assertEqual(check.status, record.OK)
        self.assertIn("A copy is running since", check.detail)

    def test_a_password_in_a_url_never_reaches_the_page(self):
        self.copied(3600, result="failed",
                    message="Fatal: Get https://ops:hunter2@s3.example.org/b: refused")
        check = record.check_offsite()
        self.assertNotIn("hunter2", check.summary + check.detail)

    def test_garbage_in_a_state_file_is_no_copy(self):
        (self.dir / "backup").write_bytes(b"\xff\xfe last_ok=yesterday\nresult\n")
        check = record.check_offsite()
        self.assertIn(check.status, (record.OK, record.WARN))
        self.assertNotIn("Newest copy", check.detail)

    def test_it_is_one_of_the_record_checks_after_the_backups(self):
        keys = [check.__name__ for check in record.ALL_CHECKS]
        self.assertEqual(keys[keys.index("check_backups") + 1], "check_offsite")

    @override_settings(EMAIL_BACKEND=LOCMEM, NOTICES_VIA_WORKER=False,
                       ALERT_EMAILS=["ops@example.test"])
    def test_the_alert_run_mails_a_failing_copy(self):
        Platform.objects.create(site_name="Zenobia Test", author="Tests",
                                publication_year=2026, active=True)
        self.copied(self.late(heartbeats.FAIL_AFTER), result="failed",
                    message="backup failed (restic exit 1): Fatal: Access Denied.")
        with mock.patch.object(record, "ALL_CHECKS", (record.check_offsite,)), \
                self.captureOnCommitCallbacks(execute=True):
            alerts.run()
        [alert] = mail.outbox
        self.assertEqual(alert.subject, "Zenobia Test: Off-site backup is failing")
        self.assertIn("The last off-site copy failed: backup failed (restic exit 1)",
                      alert.body)
