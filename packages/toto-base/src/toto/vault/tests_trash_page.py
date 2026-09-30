"""The Trash tab (``trash/``, 2026-10-01; ``trash_views.py``): who sees which
trashed files and may act on them — the owner, another member, a superuser
without and with the Superuser plan, an owner lacking the bucket's
clearance; the restore (to its folder, to the root when the folder is gone,
" (restored)" when the name or key is taken); Delete for good (confirmed;
bytes, versions and version bodies gone) and Empty my trash; the audit chain.

    manage.py test toto.vault.tests_trash_page
"""

import io
import os
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.audit.models import AuditRecord
from toto.people.models import Person
from toto.socialhub.models import Clearance
from toto.vault import trash, versions
from toto.vault.models import (
    Bucket, BucketClearance, FileVersion, VaultDirectory, VaultFile, VersionBlob,
)

User = get_user_model()

URL = "/vault/trash/"


def _admin_plan_exists() -> bool:
    from toto.subscriptions.plans import admin_plan

    return admin_plan() is not None


def vault_actions():
    return list(AuditRecord.objects.filter(app_label="vault").order_by("sequence")
                .values_list("action", flat=True))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-trash-page-"), VAULT_TRASH_DAYS=30)
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform

        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.other = User.objects.create_user("other", password="pw")
        cls.root = User.objects.create_superuser("root", "r@e.org", "pw")
        # `bootstrap_plans` puts `root` on the Superuser plan; `bare_root`,
        # made after, is a superuser without it.
        call_command("bootstrap_plans", stdout=io.StringIO())
        cls.root = User.objects.get(pk=cls.root.pk)
        cls.bare_root = User.objects.create_superuser("bareroot", "b@e.org", "pw")
        cls.bucket = Bucket.objects.create(name="Owned", slug="owned", owner=cls.owner)
        cls.folder = VaultDirectory.objects.create(name="folder", bucket=cls.bucket,
                                                   owner=cls.owner)
        # A bucket kept to a clearance the owner does not hold.
        cls.payroll = Clearance.objects.create(name="payroll", slug="payroll")
        cls.kept = Bucket.objects.create(name="Kept", slug="kept", owner=cls.owner)
        BucketClearance.objects.create(bucket=cls.kept, clearance=cls.payroll)

    def file(self, key, body=b"hello", *, owner=None, bucket=None, directory="folder"):
        vault_file = VaultFile(owner=owner or self.owner, title=f"{key}.txt", key=key,
                               file_type="text", bucket=bucket or self.bucket,
                               directory=self.folder if directory == "folder" else directory)
        vault_file.file.save(f"{key}.txt", ContentFile(body), save=False)
        vault_file.save()
        return vault_file

    def trashed(self, key, **kwargs):
        vault_file = self.file(key, **kwargs)
        vault_file.trash(vault_file.owner)
        return VaultFile.all_objects.get(pk=vault_file.pk)

    def as_(self, user, *, csrf=False):
        client = Client(enforce_csrf_checks=csrf)
        client.force_login(user)
        return client

    def listed(self, user):
        response = self.as_(user).get(URL)
        self.assertEqual(response.status_code, 200)
        return {r["file"].pk for r in response.context["rows"]}

    def restore(self, user, vault_file):
        return self.as_(user).post(reverse("vault:trash_restore", args=[vault_file.pk]))

    def purge(self, user, vault_file, confirm="yes"):
        return self.as_(user).post(reverse("vault:trash_purge", args=[vault_file.pk]),
                                   {"confirm": confirm})


class WhoSeesTests(_Fixture):
    def setUp(self):
        self.mine = self.trashed("mine")
        self.theirs = self.trashed("theirs", owner=self.other)
        self.kept_file = self.trashed("secret", bucket=self.kept, directory=None)
        self.live = self.file("live")

    def test_the_owner_sees_their_own_trash_only(self):
        self.assertEqual(self.listed(self.owner), {self.mine.pk})

    def test_another_member_sees_their_own_not_the_owner_s(self):
        self.assertEqual(self.listed(self.other), {self.theirs.pk})

    def test_an_owner_lacking_the_bucket_s_clearance_does_not_see_it(self):
        self.assertNotIn(self.kept_file.pk, self.listed(self.owner))

    def test_a_holder_of_the_clearance_sees_their_file_there(self):
        Person.objects.create(user=self.owner, display_name="O").clearances.add(self.payroll)
        self.assertIn(self.kept_file.pk, self.listed(self.owner))

    def test_a_superuser_without_the_plan_sees_only_their_own(self):
        if not _admin_plan_exists():
            self.skipTest("this host's ladder has no plan for admins")
        own = self.trashed("rootfile", owner=self.bare_root)
        self.assertEqual(self.listed(self.bare_root), {own.pk})

    def test_a_superuser_on_the_plan_sees_every_trashed_file(self):
        self.assertEqual(self.listed(self.root),
                         {self.mine.pk, self.theirs.pk, self.kept_file.pk})

    def test_a_visitor_goes_to_the_login_page(self):
        response = Client().get(URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn("next=", response["Location"])

    def test_the_page_shows_the_days_and_the_tab(self):
        page = self.as_(self.owner).get(URL).content.decode()
        self.assertIn('data-testid="vault-trash-tab"', page)
        self.assertIn('data-testid="trash-table"', page)
        self.assertIn('data-testid="trash-cards"', page)
        self.assertIn("30 days", page)

    def test_days_left_counts_down_from_the_setting(self):
        self.mine.trashed_at = timezone.now() - timedelta(days=10, hours=1)
        self.assertEqual(trash.days_left(self.mine), 20)
        self.mine.trashed_at = timezone.now() - timedelta(days=40)
        self.assertEqual(trash.days_left(self.mine), 0)


class ActTests(_Fixture):
    """Restore and Delete for good, per person: the doors look the file up in
    the same queryset the page lists, so what one does not see is a 404."""

    def setUp(self):
        self.mine = self.trashed("mine")
        self.kept_file = self.trashed("secret", bucket=self.kept, directory=None)

    def test_another_member_cannot_restore_or_purge(self):
        self.assertEqual(self.restore(self.other, self.mine).status_code, 404)
        self.assertEqual(self.purge(self.other, self.mine).status_code, 404)
        self.assertIsNotNone(VaultFile.all_objects.get(pk=self.mine.pk).trashed_at)

    def test_a_superuser_without_the_plan_cannot_touch_another_s(self):
        if not _admin_plan_exists():
            self.skipTest("this host's ladder has no plan for admins")
        self.assertEqual(self.restore(self.bare_root, self.mine).status_code, 404)
        self.assertEqual(self.purge(self.bare_root, self.mine).status_code, 404)

    def test_the_owner_lacking_the_clearance_cannot_restore_or_purge(self):
        self.assertEqual(self.restore(self.owner, self.kept_file).status_code, 404)
        self.assertEqual(self.purge(self.owner, self.kept_file).status_code, 404)
        self.assertTrue(VaultFile.all_objects.filter(pk=self.kept_file.pk).exists())

    def test_the_owner_restores(self):
        self.assertEqual(self.restore(self.owner, self.mine).status_code, 302)
        self.assertTrue(VaultFile.objects.filter(pk=self.mine.pk).exists())

    def test_a_superuser_on_the_plan_restores_and_purges_another_s(self):
        self.assertEqual(self.restore(self.root, self.kept_file).status_code, 302)
        self.assertTrue(VaultFile.objects.filter(pk=self.kept_file.pk).exists())
        self.assertEqual(self.purge(self.root, self.mine).status_code, 302)
        self.assertFalse(VaultFile.all_objects.filter(pk=self.mine.pk).exists())

    def test_a_live_file_is_not_in_the_trash(self):
        live = self.file("live")
        self.assertEqual(self.restore(self.owner, live).status_code, 404)
        self.assertEqual(self.purge(self.owner, live).status_code, 404)

    def test_the_doors_want_a_post_with_csrf(self):
        url = reverse("vault:trash_restore", args=[self.mine.pk])
        self.assertEqual(self.as_(self.owner).get(url).status_code, 405)
        self.assertEqual(self.as_(self.owner, csrf=True).post(url).status_code, 403)
        self.assertIsNotNone(VaultFile.all_objects.get(pk=self.mine.pk).trashed_at)

    def test_a_visitor_cannot_post(self):
        response = Client().post(reverse("vault:trash_purge", args=[self.mine.pk]),
                                 {"confirm": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(VaultFile.all_objects.filter(pk=self.mine.pk).exists())


class RestoreTests(_Fixture):
    def messages(self, response):
        return " ".join(str(m) for m in response.context["messages"])

    def test_back_to_its_folder_under_its_name(self):
        vault_file = self.trashed("doc")
        response = self.as_(self.owner).post(
            reverse("vault:trash_restore", args=[vault_file.pk]), follow=True)
        row = VaultFile.objects.get(pk=vault_file.pk)
        self.assertEqual((row.directory, row.title, row.key), (self.folder, "doc.txt", "doc"))
        self.assertEqual((row.trashed_at, row.trashed_by, row.trashed_from), (None, None, None))
        self.assertIn("doc.txt is back in folder.", self.messages(response))

    def test_to_the_root_when_its_folder_is_gone(self):
        gone = VaultDirectory.objects.create(name="gone", bucket=self.bucket, owner=self.owner)
        vault_file = self.trashed("doc", directory=gone)
        gone.delete()                           # SET_NULLs trashed_from
        response = self.as_(self.owner).post(
            reverse("vault:trash_restore", args=[vault_file.pk]), follow=True)
        self.assertIsNone(VaultFile.objects.get(pk=vault_file.pk).directory)
        self.assertIn("the bucket's root", self.messages(response))

    def test_a_taken_name_gets_the_restored_suffix(self):
        vault_file = self.trashed("doc")
        self.file("doc")                        # a new doc.txt, same folder and key
        response = self.as_(self.owner).post(
            reverse("vault:trash_restore", args=[vault_file.pk]), follow=True)
        row = VaultFile.objects.get(pk=vault_file.pk)
        self.assertEqual((row.title, row.key), ("doc (restored).txt", "doc-restored"))
        self.assertIn("doc (restored).txt", self.messages(response))
        self.assertIn("already had its name", self.messages(response))

    def test_a_second_clash_counts_on(self):
        first = self.trashed("doc")
        self.file("doc")
        trash.restore_file(first, by=self.owner)
        second = self.trashed("doc2")
        second.title, second.key = "doc.txt", "doc"
        second.save(update_fields=["title", "key"])
        done = trash.restore_file(VaultFile.all_objects.get(pk=second.pk), by=self.owner)
        self.assertEqual(done.title, "doc (restored 2).txt")

    def test_a_key_taken_elsewhere_in_the_bucket_is_a_clash_too(self):
        vault_file = self.trashed("doc")
        self.file("doc", directory=None)        # same key, at the root
        trash.restore_file(vault_file, by=self.owner)
        row = VaultFile.objects.get(pk=vault_file.pk)
        self.assertEqual((row.directory, row.key), (self.folder, "doc-restored"))

    def test_a_bucket_being_deleted_takes_nothing_back(self):
        vault_file = self.trashed("doc")
        Bucket.objects.filter(pk=self.bucket.pk).update(deletion_requested_at=timezone.now())
        self.restore(self.owner, vault_file)
        self.assertIsNotNone(VaultFile.all_objects.get(pk=vault_file.pk).trashed_at)
        Bucket.objects.filter(pk=self.bucket.pk).update(deletion_requested_at=None)

    def test_the_restore_is_recorded(self):
        vault_file = self.trashed("secret-name")
        self.restore(self.owner, vault_file)
        entry = AuditRecord.objects.get(action=trash.FILE_RESTORED)
        self.assertEqual((entry.object_id, entry.actor_user), (str(vault_file.pk), self.owner))
        self.assertNotIn("secret-name", str(entry.metadata))
        self.assertEqual(vault_actions(), ["FILE_RESTORED"])


class PurgeTests(_Fixture):
    def test_delete_for_good_removes_bytes_versions_and_bodies(self):
        vault_file = self.file("doc", b"version one")
        version = versions.save_version(vault_file, author=self.owner, label="v1")
        blob = version.blob
        blob_path = blob.data.path
        path = vault_file.file.path
        vault_file.trash(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.purge(self.owner, vault_file)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(VaultFile.all_objects.filter(pk=vault_file.pk).exists())
        self.assertFalse(FileVersion.objects.filter(pk=version.pk).exists())
        self.assertFalse(VersionBlob.objects.filter(pk=blob.pk).exists())
        self.assertFalse(os.path.exists(path))
        self.assertFalse(os.path.exists(blob_path))

    def test_without_the_confirmation_nothing_goes(self):
        vault_file = self.trashed("doc")
        self.assertEqual(self.purge(self.owner, vault_file, confirm="").status_code, 302)
        self.assertTrue(VaultFile.all_objects.filter(pk=vault_file.pk).exists())

    def test_the_purge_is_recorded(self):
        vault_file = self.trashed("secret-name")
        self.purge(self.owner, vault_file)
        entry = AuditRecord.objects.get(action=trash.FILE_PURGED)
        self.assertEqual((entry.object_id, entry.actor_user), (str(vault_file.pk), self.owner))
        self.assertNotIn("secret-name", str(entry.metadata))
        self.assertEqual(vault_actions(), ["FILE_PURGED"])

    def test_empty_my_trash_takes_only_mine(self):
        mine = [self.trashed("a"), self.trashed("b")]
        theirs = self.trashed("c", owner=self.other)
        live = self.file("live")
        response = self.as_(self.owner).post(reverse("vault:trash_empty"), {"confirm": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(VaultFile.all_objects.filter(pk__in=[f.pk for f in mine]).exists())
        self.assertTrue(VaultFile.all_objects.filter(pk=theirs.pk).exists())
        self.assertTrue(VaultFile.objects.filter(pk=live.pk).exists())
        self.assertEqual(vault_actions(), ["FILE_PURGED", "FILE_PURGED"])

    def test_a_superuser_on_the_plan_empties_only_their_own(self):
        theirs = self.trashed("c", owner=self.other)
        self.as_(self.root).post(reverse("vault:trash_empty"), {"confirm": "yes"})
        self.assertTrue(VaultFile.all_objects.filter(pk=theirs.pk).exists())

    def test_empty_without_the_confirmation_does_nothing(self):
        vault_file = self.trashed("a")
        self.as_(self.owner).post(reverse("vault:trash_empty"))
        self.assertTrue(VaultFile.all_objects.filter(pk=vault_file.pk).exists())
