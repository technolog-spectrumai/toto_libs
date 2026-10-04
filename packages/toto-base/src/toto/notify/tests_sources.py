"""Each source tells the right people and no more (2026-10-04).

    manage.py test toto.notify.tests_sources
"""

import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.notify.models import Notification
from toto.people.models import Person
from toto.socialhub.models import Clearance, DataExport, ErasureRequest
from toto.vault.models import Bucket, BucketClearance, VaultDirectory, VaultFile
from toto.vault.transfer import TransferRun

User = get_user_model()


def told(kind=None):
    rows = Notification.objects.all()
    if kind:
        rows = rows.filter(kind=kind)
    return sorted(rows.values_list("recipient__username", flat=True))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="notify-sources-"))
class Case(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", password="pw")
        self.ada = User.objects.create_user("ada", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")
        self.eve = User.objects.create_user("eve", password="pw")
        self.people = {u.username: Person.objects.create(user=u, display_name=u.username.title())
                       for u in (self.owner, self.ada, self.bob, self.eve)}
        self.bucket = Bucket.objects.create(name="Work", slug="work", owner=self.owner)
        self.folder = VaultDirectory.objects.create(name="Papers", bucket=self.bucket,
                                                    owner=self.owner)
        Notification.objects.all().delete()

    _n = 0

    def upload(self, by, *, folder=None, public=False, title="plan.txt"):
        type(self)._n += 1
        vault_file = VaultFile(owner=by, title=title, key=f"k{self._n}", file_type="text",
                               bucket=self.bucket, directory=folder, is_public=public)
        vault_file.file.save(title, ContentFile(b"x"), save=False)
        vault_file.save()
        return vault_file


class FileTests(Case):
    def test_an_upload_tells_the_buckets_owner_and_not_the_uploader(self):
        self.upload(self.ada)
        self.assertEqual(told("vault.uploaded"), ["owner"])
        row = Notification.objects.get()
        self.assertEqual((row.actor, row.params["title"], row.params["bucket"]),
                         (self.ada, "plan.txt", "Work"))
        self.assertIn("bucket=work", row.link)

    def test_ones_own_upload_to_ones_own_bucket_tells_nobody(self):
        self.upload(self.owner)
        self.assertEqual(told(), [])

    def test_the_folders_access_list_is_told_and_a_stranger_is_not(self):
        self.folder.allowed_users.add(self.bob)
        Notification.objects.all().delete()
        self.upload(self.ada, folder=self.folder)
        self.assertEqual(told("vault.uploaded"), ["bob", "owner"])
        self.assertNotIn("eve", told())

    def test_a_kept_bucket_tells_only_the_holders_not_even_its_owner(self):
        payroll = Clearance.objects.create(name="Payroll")
        BucketClearance.objects.create(bucket=self.bucket, clearance=payroll)
        self.folder.allowed_users.add(self.bob, self.eve)
        self.people["bob"].clearances.add(payroll)
        Notification.objects.all().delete()
        self.upload(self.ada, folder=self.folder, title="salaries.csv")
        self.assertEqual(told("vault.uploaded"), ["bob"])
        for name in ("owner", "eve"):
            self.assertFalse(Notification.objects.filter(
                recipient__username=name, params__title="salaries.csv").exists())

    def test_a_burst_of_uploads_is_one_row(self):
        for n in range(5):
            self.upload(self.ada, title=f"scan{n}.txt")
        row = Notification.objects.get(recipient=self.owner)
        self.assertEqual(row.params["count"], 5)

    def test_trash_restore_and_replace(self):
        vault_file = self.upload(self.ada, folder=self.folder)
        Notification.objects.all().delete()
        fresh = VaultFile.objects.get(pk=vault_file.pk)
        fresh.trash(by=self.ada)
        self.assertEqual(told("vault.trashed"), ["owner"])
        from toto.vault.trash import restore_file

        restore_file(VaultFile.all_objects.get(pk=vault_file.pk), by=self.owner)
        self.assertEqual(told("vault.restored"), ["ada", "owner"])   # no request: nobody is "who did it"
        fresh = VaultFile.objects.get(pk=vault_file.pk)
        fresh.content_hash = "a" * 64
        fresh.save(update_fields=["content_hash"])
        fresh.content_hash = "b" * 64
        fresh.save(update_fields=["content_hash"])
        self.assertEqual(len(told("vault.replaced")), 2)

    def test_a_rename_and_a_move_tell_nobody(self):
        vault_file = VaultFile.objects.get(pk=self.upload(self.ada).pk)
        Notification.objects.all().delete()
        vault_file.title = "other.txt"
        vault_file.directory = self.folder
        vault_file.save(update_fields=["title", "directory"])
        self.assertEqual(told(), [])

    def test_a_mirrored_row_is_nobodys_act(self):
        VaultFile.objects.create(owner=self.ada, title="stub", key="stub", file_type="text",
                                 bucket=self.bucket, origin="mirror")
        self.assertEqual(told(), [])


class ShareAndClearanceTests(Case):
    def test_being_added_to_a_folders_list_tells_the_one_added(self):
        self.folder.allowed_users.add(self.bob, self.owner)
        self.assertEqual(told("vault.folder_shared"), ["bob"])
        self.assertEqual(Notification.objects.get().params["folder"], "Papers")

    def test_not_when_the_bucket_is_hidden_from_them(self):
        BucketClearance.objects.create(bucket=self.bucket,
                                       clearance=Clearance.objects.create(name="Payroll"))
        self.folder.allowed_users.add(self.bob)
        self.assertEqual(told(), [])

    def test_a_clearance_given_and_taken_tells_the_person(self):
        internal = Clearance.objects.create(name="Internal")
        self.people["ada"].clearances.add(internal)
        self.assertEqual(told("clearance.granted"), ["ada"])
        internal.members.remove(self.people["ada"])
        self.assertEqual(told("clearance.removed"), ["ada"])
        self.people["bob"].clearances.add(internal)
        internal.delete()
        self.assertEqual(told("clearance.removed"), ["ada", "bob"])

    def test_a_share_with_another_zenobia_tells_nobody(self):
        from toto.vault.peering import BucketGrant

        BucketGrant.objects.create(bucket=self.bucket, label="the other one")
        self.assertEqual(told(), [])


class JobAndAccountTests(Case):
    def test_a_finished_transfer_is_told_once_to_who_started_it(self):
        run = TransferRun.objects.create(owner=self.ada, dest_bucket=self.bucket)
        self.assertEqual(told(), [])
        run.status, run.finished_at = "success", timezone.now()
        run.save()
        run.save()
        self.assertEqual(told("job.transfer_done"), ["ada"])
        failed = TransferRun.objects.create(owner=self.bob, dest_bucket=self.bucket,
                                            status="failed", finished_at=timezone.now())
        self.assertEqual(told("job.transfer_failed"), ["bob"])
        self.assertIn(str(failed.pk), Notification.objects.get(recipient=self.bob).link)

    def test_the_copy_of_ones_data_and_a_declined_erasure(self):
        export = DataExport.objects.create(user=self.ada)
        export.status, export.finished_at = DataExport.READY, timezone.now()
        export.save()
        self.assertEqual(told("privacy.export_ready"), ["ada"])
        ticket = ErasureRequest.objects.create(user=self.bob, username="bob")
        ticket.status, ticket.handled_by, ticket.handled_at = (
            ErasureRequest.DECLINED, self.eve, timezone.now())
        ticket.save()
        row = Notification.objects.get(kind="privacy.erasure_declined")
        self.assertEqual((row.recipient, row.actor), (self.bob, None))

    def test_a_mailed_notice_is_in_the_bell_too_even_with_no_address(self):
        from toto.core.notices import send_notice

        self.assertEqual(self.ada.email, "")
        send_notice(self.ada, "password_changed", {"address": "203.0.113.7"})
        row = Notification.objects.get(kind="account.password_changed")
        self.assertEqual(row.recipient, self.ada)
        self.assertNotIn("203.0.113.7", str(row.params))
        send_notice(self.ada, "email_change_confirm", {}, to="new@example.org")
        send_notice(None, "check_alert", {}, to="ops@example.org")
        self.assertEqual(Notification.objects.count(), 1)
