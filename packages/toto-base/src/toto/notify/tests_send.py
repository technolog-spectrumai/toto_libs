"""``notify.send``: the row, the push, bursts, once, the prune (2026-10-04).

    manage.py test toto.notify.tests_send
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone, translation

from toto import notify
from toto.core import live
from toto.notify import kinds, services
from toto.notify.models import Notification
from toto.notify.testing import MEMORY_LAYER, Listener

User = get_user_model()


class Case(TestCase):
    def setUp(self):
        self.ada = User.objects.create_user("ada", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")


@override_settings(CHANNEL_LAYERS=MEMORY_LAYER)
class WithALayerTests(Case):
    def test_the_row_is_written_and_the_members_pages_are_poked(self):
        mine, theirs = Listener(live.user_group(self.ada.pk)), Listener(live.user_group(self.bob.pk))
        with self.captureOnCommitCallbacks(execute=True):
            row = notify.send(self.ada, "account.password_changed", link="/account/")
        self.assertEqual(Notification.objects.get().pk, row.pk)
        self.assertEqual(mine.messages(), [{"type": "live.notification"}])
        self.assertEqual(theirs.messages(), [])

    def test_the_push_carries_no_text_and_no_name(self):
        mine = Listener(live.user_group(self.ada.pk))
        with self.captureOnCommitCallbacks(execute=True):
            notify.send(self.ada, "vault.uploaded", actor=self.bob, title="secret-plan.pdf",
                        bucket="Payroll", bucket_id=1)
        (message,) = mine.messages()
        self.assertEqual(set(message), {"type"})

    def test_nothing_is_pushed_for_a_change_that_rolled_back(self):
        mine = Listener(live.user_group(self.ada.pk))
        with self.captureOnCommitCallbacks(execute=False):
            notify.send(self.ada, "account.password_changed")
        self.assertEqual(mine.messages(), [])


@override_settings(CHANNEL_LAYERS={})
class WithoutALayerTests(Case):
    def test_the_row_is_still_written(self):
        self.assertFalse(live.available())
        with self.captureOnCommitCallbacks(execute=True):
            row = notify.send(self.ada, "account.password_changed")
        self.assertIsNotNone(row)
        self.assertEqual(Notification.objects.filter(recipient=self.ada).count(), 1)

    def test_publish_is_a_no_op(self):
        live.publish(live.user_group(self.ada.pk), {"type": live.NOTIFICATION})


class RulesTests(Case):
    def test_nobody_is_told_what_they_did_themselves(self):
        self.assertIsNone(notify.send(self.ada, "vault.uploaded", actor=self.ada, title="a"))
        self.assertEqual(Notification.objects.count(), 0)

    def test_an_inactive_account_and_an_unknown_kind_get_nothing(self):
        self.bob.is_active = False
        self.bob.save()
        self.assertIsNone(notify.send(self.bob, "account.password_changed"))
        with self.assertLogs("toto.notify", "ERROR"):
            self.assertIsNone(notify.send(self.ada, "no.such.kind"))
        self.assertIsNone(notify.send(None, "account.password_changed"))
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

    def test_once_is_said_once_read_or_not(self):
        row = notify.send(self.ada, "job.transfer_done", once="transfer:5", bucket="Work")
        services.mark_read(self.ada, row.pk)
        self.assertIsNone(notify.send(self.ada, "job.transfer_done", once="transfer:5", bucket="Work"))
        self.assertEqual(Notification.objects.count(), 1)

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
        read_old = notify.send(self.ada, "account.password_changed")
        read_new = notify.send(self.ada, "account.new_sign_in")
        unread_old = notify.send(self.ada, "account.email_changed")
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
        notify.send(self.bob, "account.password_changed")
        self.bob.delete()
        self.assertEqual(Notification.objects.count(), 0)


class PersonalDataTests(Case):
    def test_a_members_own_notifications_are_in_the_copy_of_their_data(self):
        from toto.core import personal_data

        notify.send(self.ada, "vault.uploaded", actor=self.bob, title="a.txt", bucket="W")
        notify.send(self.bob, "account.password_changed")
        plugin = [p for p in personal_data.plugins() if p.get_key() == "notify"][0]
        (table,) = plugin.tables(self.ada)
        self.assertEqual(table.name, "notifications")
        self.assertEqual([row["kind"] for row in table.rows], ["vault.uploaded"])
        self.assertNotIn("actor_id", table.rows[0])
