"""Only about a bucket you own: who is told of a file, and what tells nobody
any more (2026-10-04).

    manage.py test toto.notify.tests_sources
"""

import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.notify import kinds
from toto.notify.models import Notification
from toto.people.models import Person
from toto.socialhub.models import Clearance, Community, DataExport, ErasureRequest
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

    def upload(self, by, *, folder=None, public=False, title="plan.txt", bucket=None):
        type(self)._n += 1
        vault_file = VaultFile(owner=by, title=title, key=f"k{self._n}", file_type="text",
                               bucket=bucket or self.bucket, directory=folder, is_public=public)
        vault_file.file.save(title, ContentFile(b"x"), save=False)
        vault_file.save()
        return vault_file


class OwnedBucketTests(Case):
    def test_an_upload_tells_the_buckets_owner_and_not_the_uploader(self):
        self.upload(self.ada)
        self.assertEqual(told(), ["owner"])
        row = Notification.objects.get()
        self.assertEqual((row.kind, row.actor, row.params["title"], row.params["bucket"]),
                         ("vault.uploaded", self.ada, "plan.txt", "Work"))
        self.assertIn("bucket=work", row.link)

    def test_ones_own_upload_to_ones_own_bucket_tells_nobody(self):
        self.upload(self.owner)
        self.assertEqual(told(), [])

    def test_the_folders_access_list_and_the_files_owner_are_not_told(self):
        self.folder.allowed_users.add(self.bob, self.eve)
        vault_file = self.upload(self.ada, folder=self.folder)
        self.assertEqual(told(), ["owner"])
        Notification.objects.all().delete()
        # Ada's file, trashed by nobody in particular: still the bucket's
        # owner alone, never Ada (it is not her bucket).
        VaultFile.objects.get(pk=vault_file.pk).trash()
        self.assertEqual(told(), ["owner"])

    def test_a_bucket_somebody_else_owns_tells_its_owner_not_me(self):
        theirs = Bucket.objects.create(name="Theirs", slug="theirs", owner=self.bob)
        Notification.objects.all().delete()
        self.upload(self.ada, bucket=theirs, public=True)
        self.assertEqual(told(), ["bob"])

    def test_a_bucket_with_no_owner_tells_nobody(self):
        nobodys = Bucket.objects.create(name="Shared", slug="shared")
        self.upload(self.ada, bucket=nobodys, public=True)
        self.assertEqual(told(), [])

    def test_a_kept_bucket_tells_its_owner_only_if_they_hold_a_clearance(self):
        payroll = Clearance.objects.create(name="Payroll")
        BucketClearance.objects.create(bucket=self.bucket, clearance=payroll)
        self.people["bob"].clearances.add(payroll)
        self.folder.allowed_users.add(self.bob)
        self.upload(self.ada, folder=self.folder, title="salaries.csv")
        self.assertEqual(told(), [])            # no owner bypass, and Bob owns nothing
        self.people["owner"].clearances.add(payroll)
        self.upload(self.ada, folder=self.folder, title="bonuses.csv")
        self.assertEqual(told(), ["owner"])
        self.assertEqual(Notification.objects.get().params["title"], "bonuses.csv")

    def test_an_owner_who_cannot_sign_in_is_told_nothing(self):
        User.objects.filter(pk=self.owner.pk).update(is_active=False)
        self.upload(self.ada)
        self.assertEqual(told(), [])

    def test_a_burst_of_uploads_is_one_row(self):
        for n in range(5):
            self.upload(self.ada, title=f"scan{n}.txt")
        row = Notification.objects.get(recipient=self.owner)
        self.assertEqual(row.params["count"], 5)

    def test_trash_restore_and_replace(self):
        vault_file = self.upload(self.ada, folder=self.folder)
        Notification.objects.all().delete()
        VaultFile.objects.get(pk=vault_file.pk).trash(by=self.ada)
        self.assertEqual(told("vault.trashed"), ["owner"])
        from toto.vault.trash import restore_file

        restore_file(VaultFile.all_objects.get(pk=vault_file.pk), by=self.ada)
        self.assertEqual(told("vault.restored"), ["owner"])
        fresh = VaultFile.objects.get(pk=vault_file.pk)
        fresh.content_hash = "a" * 64
        fresh.save(update_fields=["content_hash"])
        fresh.content_hash = "b" * 64
        fresh.save(update_fields=["content_hash"])
        self.assertEqual(told("vault.replaced"), ["owner"])

    def test_a_rename_a_move_and_a_delete_outright_tell_nobody(self):
        vault_file = VaultFile.objects.get(pk=self.upload(self.ada).pk)
        Notification.objects.all().delete()
        vault_file.title = "other.txt"
        vault_file.directory = self.folder
        vault_file.save(update_fields=["title", "directory"])
        vault_file.delete()
        self.assertEqual(told(), [])

    def test_a_mirrored_row_is_nobodys_act(self):
        VaultFile.objects.create(owner=self.ada, title="stub", key="stub", file_type="text",
                                 bucket=self.bucket, origin="mirror")
        self.assertEqual(told(), [])


class RemovedSourcesTests(Case):
    """What told somebody on the morning of 2026-10-04 and tells nobody now."""

    def test_there_are_four_kinds_and_all_are_about_a_bucket(self):
        self.assertEqual(sorted(kinds.KINDS), ["vault.replaced", "vault.restored",
                                               "vault.trashed", "vault.uploaded"])
        self.assertTrue(all(kind.bucket_scoped for kind in kinds.KINDS.values()))

    def test_a_folders_access_list_and_a_bucket_made_for_somebody(self):
        self.folder.allowed_users.add(self.bob, self.owner)
        Bucket.objects.create(name="Given", slug="given", owner=self.eve)
        self.assertEqual(told(), [])

    def test_a_clearance_given_taken_and_deleted(self):
        internal = Clearance.objects.create(name="Internal")
        self.people["ada"].clearances.add(internal)
        internal.members.remove(self.people["ada"])
        self.people["bob"].clearances.add(internal)
        internal.delete()
        self.assertEqual(told(), [])

    def test_a_finished_transfer_the_copy_of_ones_data_and_a_declined_erasure(self):
        run = TransferRun.objects.create(owner=self.ada, dest_bucket=self.bucket)
        run.status, run.finished_at = "success", timezone.now()
        run.save()
        TransferRun.objects.create(owner=self.bob, dest_bucket=self.bucket,
                                   status="failed", finished_at=timezone.now())
        export = DataExport.objects.create(user=self.ada)
        export.status, export.finished_at = DataExport.READY, timezone.now()
        export.save()
        ticket = ErasureRequest.objects.create(user=self.bob, username="bob")
        ticket.status, ticket.handled_by, ticket.handled_at = (
            ErasureRequest.DECLINED, self.eve, timezone.now())
        ticket.save()
        self.assertEqual(told(), [])

    def test_a_mailed_notice_is_not_in_the_bell(self):
        from toto.core import notices

        notices.send_notice(self.ada, "password_changed", {"address": "203.0.113.7"})
        notices.send_notice(self.ada, "new_sign_in", {"address": "203.0.113.7"})
        self.assertEqual(told(), [])
        self.assertFalse(hasattr(notices, "_bell"))

    def test_signing_in_signing_out_and_joining_tell_nobody(self):
        from django.contrib.auth.signals import user_logged_in, user_logged_out

        club = Community.objects.create(name="Club", slug="club")
        self.people["ada"].communities.add(club)
        self.people["bob"].communities.add(club)
        self.assertTrue(self.client.login(username="bob", password="pw"))
        self.client.logout()
        self.assertEqual(told(), [])
        for signal in (user_logged_in, user_logged_out):
            listeners = [str(receiver[0]) for receiver in signal.receivers]
            self.assertFalse([name for name in listeners if "notify" in name], listeners)

    def test_no_presence_is_left_in_the_library(self):
        import importlib.util

        from toto.core import user_sessions
        from toto.socialhub.forms import AccountProfileForm

        self.assertIsNone(importlib.util.find_spec("toto.notify.presence"))
        self.assertNotIn("show_online", [field.name for field in Person._meta.get_fields()])
        self.assertNotIn("show_online", AccountProfileForm.Meta.fields)
        self.assertFalse(hasattr(user_sessions, "session_ended"))
