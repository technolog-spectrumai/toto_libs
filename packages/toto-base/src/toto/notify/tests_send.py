"""``notify.send``: the row and no signal, bursts, the prune (2026-10-04).

    manage.py test toto.notify.tests_send
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone, translation

from toto import notify
from toto.notify import kinds, services
from toto.notify.models import Notification

User = get_user_model()


class Case(TestCase):
    def setUp(self):
        cache.clear()
        self.ada = User.objects.create_user("ada", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")


class NoSignalTests(Case):
    """A notification is a row and nothing else (2026-10-06): nobody is
    woken, nothing is published, nothing waits for it."""

    def test_send_writes_the_row_and_tells_no_page(self):
        from unittest import mock

        with mock.patch("django.core.cache.cache.set") as stamped, \
                self.captureOnCommitCallbacks(execute=True) as after_commit:
            row = notify.send(self.ada, "vault.uploaded", link="/vault/")
            services.mark_read(self.ada, row.pk)
            notify.send(self.ada, "vault.trashed")
            services.mark_all_read(self.ada)
        self.assertEqual(Notification.objects.filter(recipient=self.ada).count(), 2)
        self.assertEqual(after_commit, [])
        stamped.assert_not_called()

    def test_the_signal_and_the_digest_are_gone(self):
        import importlib.util

        for gone in ("_signal", "digest"):
            self.assertFalse(hasattr(services, gone), gone)
        for module in ("toto.notify.wait", "toto.core.live"):
            with self.subTest(module=module):
                self.assertIsNone(importlib.util.find_spec(module))

    def test_a_cache_that_is_away_never_fails_the_send(self):
        from unittest import mock

        with mock.patch("django.core.cache.cache.set", side_effect=RuntimeError("down")), \
                mock.patch("django.core.cache.cache.get", side_effect=RuntimeError("down")):
            row = notify.send(self.ada, "vault.uploaded")
        self.assertIsNotNone(row)
        self.assertEqual(Notification.objects.filter(recipient=self.ada).count(), 1)


class RulesTests(Case):
    def test_nobody_is_told_what_they_did_themselves(self):
        self.assertIsNone(notify.send(self.ada, "vault.uploaded", actor=self.ada, title="a"))
        self.assertEqual(Notification.objects.count(), 0)

    def test_an_inactive_account_and_an_unknown_kind_get_nothing(self):
        self.bob.is_active = False
        self.bob.save()
        self.assertIsNone(notify.send(self.bob, "vault.uploaded"))
        for gone in ("no.such.kind", "account.password_changed", "job.transfer_done",
                     "clearance.granted", "community.joined"):
            with self.subTest(kind=gone), self.assertLogs("toto.notify", "ERROR"):
                self.assertIsNone(notify.send(self.ada, gone))
        self.assertIsNone(notify.send(None, "vault.uploaded"))
        self.assertEqual(Notification.objects.count(), 0)

    def test_a_burst_folds_into_one_row_with_a_count(self):
        for title in ("a.png", "b.png", "c.png"):
            notify.send(self.ada, "vault.uploaded", actor=self.bob, collapse="uploaded:7",
                        title=title, bucket="Work", bucket_id=7)
        row = Notification.objects.get()
        self.assertEqual(row.params["count"], 3)
        self.assertEqual(kinds.text_of(row.kind, row.params), "3 files were uploaded to Work")

    def test_a_read_or_old_row_starts_a_new_one(self):
        first = notify.send(self.ada, "vault.uploaded", collapse="uploaded:7", title="a", bucket="W")
        services.mark_read(self.ada, first.pk)
        notify.send(self.ada, "vault.uploaded", collapse="uploaded:7", title="b", bucket="W")
        self.assertEqual(Notification.objects.count(), 2)
        Notification.objects.update(read_at=None, created=timezone.now() - timedelta(hours=1))
        notify.send(self.ada, "vault.uploaded", collapse="uploaded:7", title="c", bucket="W")
        self.assertEqual(Notification.objects.count(), 3)

    def test_a_row_of_a_kind_that_left_is_neither_listed_nor_counted(self):
        notify.send(self.ada, "vault.uploaded", title="a", bucket="W")
        Notification.objects.create(recipient=self.ada, kind="account.new_sign_in")
        self.assertEqual(services.unread_count(self.ada), 1)

    def test_the_sentence_is_made_when_read_in_the_readers_language(self):
        row = notify.send(self.ada, "vault.uploaded", title="plan.pdf", bucket="Work")
        self.assertNotIn("uploaded", str(row.params))
        self.assertEqual(kinds.text_of(row.kind, row.params), "plan.pdf was uploaded to Work")
        with translation.override("pl"):
            self.assertIn("plan.pdf", kinds.text_of(row.kind, row.params))
        self.assertEqual(kinds.text_of("gone.kind", {}), "")

    def test_send_never_raises(self):
        with self.assertLogs("toto.notify", "WARNING"):
            self.assertIsNone(notify.send(self.ada, "vault.uploaded", link=object(),
                                          title="a", collapse=5))


class PruneTests(Case):
    def test_read_rows_older_than_thirty_days_go_and_unread_ones_wait(self):
        old = timezone.now() - timedelta(days=31)
        read_old = notify.send(self.ada, "vault.uploaded")
        read_new = notify.send(self.ada, "vault.replaced")
        unread_old = notify.send(self.ada, "vault.trashed")
        Notification.objects.filter(pk=read_old.pk).update(read_at=old, created=old)
        Notification.objects.filter(pk=read_new.pk).update(read_at=timezone.now())
        Notification.objects.filter(pk=unread_old.pk).update(created=old)
        self.assertEqual(services.prune(), 1)
        self.assertEqual(set(Notification.objects.values_list("pk", flat=True)),
                         {read_new.pk, unread_old.pk})
        self.assertEqual(services.prune(), 0)

    def test_the_beat_names_the_task_and_the_worker_finds_it(self):
        from toto.notify import tasks
        from toto.registry import TASK_MODULES
        from toto.schedules import beat_schedule

        entry = beat_schedule(notify_prune=True)["notify-prune-read"]
        self.assertEqual(entry["task"], tasks.TASK_NAME)
        self.assertIn("toto.notify", TASK_MODULES)
        self.assertEqual(tasks.prune_read(), {"deleted": 0})

    def test_erasing_a_member_takes_what_they_did_out_of_other_bells(self):
        notify.send(self.ada, "vault.uploaded", actor=self.bob, title="a", bucket="W")
        notify.send(self.bob, "vault.trashed", title="b", bucket="W")
        self.bob.delete()
        self.assertEqual(Notification.objects.count(), 0)


class PersonalDataTests(Case):
    def test_a_members_own_notifications_are_in_the_copy_of_their_data(self):
        from toto.core import personal_data

        notify.send(self.ada, "vault.uploaded", actor=self.bob, title="a.txt", bucket="W")
        notify.send(self.bob, "vault.trashed", title="b.txt", bucket="W")
        plugin = [p for p in personal_data.plugins() if p.get_key() == "notify"][0]
        (table,) = plugin.tables(self.ada)
        self.assertEqual(table.name, "notifications")
        self.assertEqual([row["kind"] for row in table.rows], ["vault.uploaded"])
        self.assertNotIn("actor_id", table.rows[0])
