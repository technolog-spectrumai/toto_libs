"""The vault browser (``vault:public_list``): which files and folders a
reader is shown, and the per-row facts the tree component acts on — upload
targets, create rights, the metrics link, quota badges.
"""

import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Clearance
from toto.vault.models import (Bucket, BucketClearance, FileGateway, VaultDirectory, VaultFile)

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-more-listing-"))
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.reader = User.objects.create_user("reader", password="pw")
        cls.root = User.objects.create_superuser("root", "r@e.com", "pw")
        cls.bucket = Bucket.objects.create(name="Alpha", slug="alpha", owner=cls.owner)
        cls.other_bucket = Bucket.objects.create(name="Beta", slug="beta", owner=cls.reader)

    _n = 0

    def file(self, title, *, owner=None, public=False, directory=None, bucket="default",
             encrypted=False, size=None):
        type(self)._n += 1
        vault_file = VaultFile(owner=owner or self.owner, title=title, key=f"l-{self._n}",
                               file_type="text", is_public=public, directory=directory,
                               is_encrypted=encrypted,
                               bucket=self.bucket if bucket == "default" else bucket)
        vault_file.file.save(title, ContentFile(b"x"), save=False)
        if size is not None:
            vault_file.file_size_bytes = size
        vault_file.save()
        return vault_file

    def folder(self, name, *, parent=None, bucket=None, acl=()):
        directory = VaultDirectory.objects.create(name=name, bucket=bucket or self.bucket,
                                                  owner=self.owner, parent=parent)
        if acl:
            directory.allowed_users.add(*acl)
        return directory

    def listing(self, user=None, **query):
        if user is not None:
            self.client.force_login(user)
        response = self.client.get(reverse("vault:public_list"), query)
        self.assertEqual(response.status_code, 200)
        return response

    def titles(self, response):
        return sorted(i["title"] for i in response.context["flat_items"] if i["t"] == "file")

    def dirs(self, response):
        return {i["name"]: i for i in response.context["flat_items"] if i["t"] == "dir"}


class WhoSeesWhatTests(_Fixture):
    def test_a_logged_out_visitor_is_sent_to_log_in_on_this_host(self):
        response = self.client.get(reverse("vault:public_list"))
        self.assertEqual(response.status_code, 302)

    def test_a_reader_sees_public_files_but_not_someone_elses_private_ones(self):
        self.file("open.txt", public=True)
        self.file("private.txt")
        self.assertEqual(self.titles(self.listing(self.reader)), ["open.txt"])

    def test_a_member_also_sees_their_own_private_files_and_no_one_elses(self):
        self.file("open.txt", public=True)
        self.file("owners.txt")
        self.file("mine.txt", owner=self.reader, bucket=self.other_bucket)
        self.assertEqual(self.titles(self.listing(self.reader)), ["mine.txt", "open.txt"])

    def test_a_public_file_in_a_kept_bucket_is_listed_to_its_holders_only(self):
        clearance = Clearance.objects.create(name="internal", slug="internal")
        self.file("kept.txt", public=True)
        BucketClearance.objects.create(bucket=self.bucket, clearance=clearance)
        self.assertEqual(self.titles(self.listing(self.reader)), [])
        self.assertEqual(self.titles(self.listing(self.owner)), [])      # its owner too
        self.assertEqual(self.titles(self.listing(self.root)), ["kept.txt"])
        Person.objects.create(user=self.reader, display_name="R").clearances.add(clearance)
        self.assertEqual(self.titles(self.listing(self.reader)), ["kept.txt"])

    def test_a_holder_is_listed_a_private_file_in_a_kept_bucket(self):
        clearance = Clearance.objects.create(name="internal", slug="internal")
        self.file("private.txt")
        BucketClearance.objects.create(bucket=self.bucket, clearance=clearance)
        Person.objects.create(user=self.reader, display_name="R").clearances.add(clearance)
        self.assertEqual(self.titles(self.listing(self.reader)), ["private.txt"])

    def test_a_restricted_folder_and_everything_in_it_is_hidden_from_outsiders(self):
        locked = self.folder("locked", acl=[self.owner])
        self.file("inside.txt", public=True, directory=locked)
        response = self.listing(self.reader)
        self.assertNotIn("locked", self.dirs(response))
        self.assertEqual(self.titles(response), [])
        response = self.listing(self.owner)
        self.assertTrue(self.dirs(response)["locked"]["locked"])
        self.assertEqual(self.titles(response), ["inside.txt"])

    def test_the_bucket_filter_scopes_files_and_folders(self):
        self.folder("a-dir")
        self.folder("b-dir", bucket=self.other_bucket)
        self.file("a.txt", public=True)
        self.file("b.txt", public=True, bucket=self.other_bucket)
        response = self.listing(self.owner, bucket="beta")
        self.assertEqual(self.titles(response), ["b.txt"])
        self.assertEqual(set(self.dirs(response)), {"b-dir"})
        self.assertEqual(response.context["selected_bucket"], "beta")


class RowFactsTests(_Fixture):
    def test_nesting_is_carried_as_parent_and_depth(self):
        top = self.folder("top")
        sub = self.folder("sub", parent=top)
        self.file("deep.txt", public=True, directory=sub)
        response = self.listing(self.owner)
        dirs = self.dirs(response)
        self.assertEqual((dirs["top"]["depth"], dirs["top"]["pid"], dirs["top"]["n_dirs"]),
                         (0, None, 1))
        self.assertEqual((dirs["sub"]["depth"], dirs["sub"]["pid"], dirs["sub"]["n_files"]),
                         (1, top.pk, 1))
        [row] = [i for i in response.context["flat_items"] if i["t"] == "file"]
        self.assertEqual((row["pid"], row["depth"]), (sub.pk, 2))
        self.assertEqual(response.context["total_dirs"], 2)
        self.assertEqual(response.context["total_files"], 1)

    def test_a_gateway_folder_and_its_subfolders_offer_the_gateway(self):
        inbox = self.folder("inbox")
        below = self.folder("below", parent=inbox)
        self.folder("unrelated")
        FileGateway.objects.create(directory=inbox, name="gw")
        dirs = self.dirs(self.listing(self.reader))
        page = reverse("vault:gateway_page", kwargs={"dir_pk": inbox.pk})
        self.assertEqual(dirs["inbox"]["upload_url"], page)
        self.assertEqual(dirs["below"]["upload_url"], f"{page}?target_dir={below.pk}")
        self.assertEqual(dirs["unrelated"]["upload_url"], "")

    def test_only_folders_in_my_own_buckets_offer_create(self):
        self.folder("theirs")
        self.folder("mine", bucket=self.other_bucket)
        dirs = self.dirs(self.listing(self.reader))
        self.assertTrue(dirs["mine"]["can_create"])
        self.assertFalse(dirs["theirs"]["can_create"])

    def test_an_encrypted_file_keeps_its_raw_url_but_offers_no_open_link(self):
        f = self.file("sealed.txt", encrypted=True)
        [row] = [i for i in self.listing(self.owner).context["flat_items"] if i["t"] == "file"]
        self.assertEqual(row["url"], "")
        self.assertEqual(row["raw_url"], f.get_public_url())
        self.assertTrue(row["encrypted"])
        self.assertEqual(row["editor_url"], "")
        self.assertEqual(row["play_url"], "")

    def test_the_metrics_link_is_offered_only_where_it_opens(self):
        self.assertFalse(self.listing(self.owner).context["may_see_metrics"])
        self.assertTrue(self.listing(self.owner, bucket="alpha").context["may_see_metrics"])
        self.assertFalse(self.listing(self.reader, bucket="alpha").context["may_see_metrics"])
        self.assertTrue(self.listing(self.root, bucket="alpha").context["may_see_metrics"])

    def test_the_quota_badge_flags_a_bucket_over_its_quota(self):
        Bucket.objects.filter(pk=self.bucket.pk).update(storage_quota_mb=1)
        self.file("big.bin", size=3 * 1_048_576)
        info = self.listing(self.owner).context["bucket_quota_info"]
        self.assertEqual(info[self.bucket.pk]["used_mb"], 3.0)
        self.assertTrue(info[self.bucket.pk]["over"])
        self.assertEqual(info[self.bucket.pk]["pct"], 100)
        self.assertIsNone(info[self.other_bucket.pk]["quota_mb"])
        self.assertFalse(info[self.other_bucket.pk]["over"])

    def test_the_rename_dialog_is_given_every_file_type(self):
        context = self.listing(self.owner).context
        self.assertEqual(context["file_types"], VaultFile.FILE_TYPES)
        self.assertFalse(context["zip_enabled"])
