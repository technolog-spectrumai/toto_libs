"""A bucket kept to clearances (2026-09-30): its files are read by superusers
and by holders of one of the bucket's clearances, and by nobody else — not
their owner, not through the public flag, not through a folder's ACL, not
through the bucket's owner. A file in a bucket with none (or in no bucket) is
the vault as it was. Only a superuser on the Superuser plan sets a bucket's
clearances, on the bucket's page; every vault door asks the one rule."""

import io
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.db.models import ProtectedError
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from toto.audit.models import AuditRecord
from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Clearance, Community
from toto.vault import clearances
from toto.vault.access import may_read
from toto.vault.filetree import accessible_files
from toto.vault.models import Bucket, BucketClearance, VaultDirectory, VaultFile
from toto.vault.version_views import _file_for

User = get_user_model()

ACTION = "VAULT.BUCKET.CLEARANCES_CHANGED"


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-clearances-"))
class BucketClearanceTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.internal = Clearance.objects.create(name="internal", slug="internal")
        cls.devs = Community.objects.create(name="devs", slug="devs")
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.member = User.objects.create_user("member", password="pw")
        Person.objects.create(user=cls.member, display_name="Member").clearances.add(cls.internal)
        cls.stranger = User.objects.create_user("stranger", password="pw")
        Person.objects.create(user=cls.stranger, display_name="Stranger").communities.add(cls.devs)
        cls.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        cls.root = User.objects.create_superuser("root", "r@e.com", "pw")
        # Setting clearances needs the Superuser plan (2026-10-01):
        # `bootstrap_plans` puts `root` on it.
        call_command("bootstrap_plans", stdout=io.StringIO())
        cls.root = User.objects.get(pk=cls.root.pk)
        cls.bucket = Bucket.objects.create(name="Kept", slug="kept", owner=cls.owner)
        BucketClearance.objects.create(bucket=cls.bucket, clearance=cls.internal)
        cls.open_bucket = Bucket.objects.create(name="Open", slug="open", owner=cls.owner)

    _n = 0

    def file(self, *, public=False, kept=True, directory=None, title="deck.txt", owner=None):
        type(self)._n += 1
        vault_file = VaultFile(owner=owner or self.owner, title=title, key=f"f-{self._n}",
                               file_type="text", is_public=public,
                               bucket=self.bucket if kept else self.open_bucket,
                               directory=directory)
        vault_file.file.save(title, ContentFile(b"the contents"), save=False)
        vault_file.save()
        return vault_file


class RuleTests(BucketClearanceTestCase):
    def test_a_holder_and_a_superuser_read_it(self):
        f = self.file()
        for user in (self.member, self.root):
            self.assertTrue(may_read(user, f), user)

    def test_its_owner_does_not_even_when_public(self):
        f = self.file(public=True)
        self.assertFalse(may_read(self.owner, f))
        f.owner = self.stranger
        f.save()
        self.assertFalse(may_read(self.stranger, f))

    def test_a_stranger_staff_and_a_visitor_do_not_even_when_public(self):
        f = self.file(public=True)
        self.assertFalse(may_read(self.stranger, f))
        self.assertFalse(may_read(self.staff, f))
        self.assertFalse(may_read(None, f))                     # anonymous

    def test_a_functional_community_grants_nothing(self):
        self.assertFalse(may_read(self.stranger, self.file()))  # in devs only

    def test_a_folder_acl_and_the_bucket_owner_no_longer_open_it(self):
        directory = VaultDirectory.objects.create(name="d", bucket=self.bucket, owner=self.owner)
        directory.allowed_users.add(self.stranger)
        self.assertFalse(may_read(self.stranger, self.file(directory=directory)))
        # The bucket's owner holds nothing: its files are missing to them.
        self.assertFalse(may_read(self.owner, self.file(owner=self.stranger)))

    def test_a_holder_reads_a_private_file_they_have_no_claim_on(self):
        self.assertTrue(may_read(self.member, self.file(public=False)))

    def test_an_unkept_bucket_is_the_vault_as_it_was(self):
        self.assertTrue(may_read(self.stranger, self.file(public=True, kept=False)))
        self.assertFalse(may_read(self.stranger, self.file(kept=False)))
        self.assertTrue(may_read(self.owner, self.file(kept=False)))
        self.assertFalse(may_read(self.member, self.file(kept=False)))  # a clearance opens nothing here

    def test_a_file_in_no_bucket_is_never_kept(self):
        f = self.file(public=True)
        f.bucket = None
        f.save()
        self.assertTrue(may_read(self.stranger, f))
        self.assertTrue(may_read(None, f))

    def test_the_queryset_agrees(self):
        kept, open_ = self.file(public=True), self.file(public=True, kept=False, title="open.txt")
        self.assertEqual(set(accessible_files(self.stranger)), {open_})
        self.assertEqual(set(accessible_files(self.owner)), {open_})
        self.assertEqual(set(accessible_files(self.member)), {kept, open_})
        self.assertEqual(set(accessible_files(self.root)), {kept, open_})

    def test_the_versions_door_and_the_browser(self):
        f = self.file(public=True)
        request = RequestFactory().get("/")
        for user in (self.staff, self.owner):
            request.user = user
            with self.assertRaises(Http404):
                _file_for(request, f.pk)
        request.user = self.member
        self.assertEqual(_file_for(request, f.pk), f)
        for user in (self.stranger, self.owner):
            self.client.force_login(user)
            body = self.client.get(reverse("vault:public_list")).content.decode()
            self.assertNotIn("deck.txt", body)
        self.client.force_login(self.member)
        self.assertIn("deck.txt", self.client.get(reverse("vault:public_list")).content.decode())

    def test_the_download_door(self):
        f = self.file(public=True)
        url = reverse("vault:public_file", args=[self.bucket.slug, f.key])
        for user in (self.stranger, self.owner):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 404, user)
        self.client.logout()
        self.assertNotEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_the_owners_own_doors_are_missing_too(self):
        f = self.file(public=True)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("vault:api_file_detail", args=[f.key])).status_code, 404)
        listed = self.client.get(reverse("vault:api_file_list")).json()["files"]
        self.assertNotIn(f.key, [row["key"] for row in listed])
        self.assertEqual(self.client.post(reverse("vault:rename_file"),
                                          {"file_pk": f.pk, "title": "x.txt"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("vault:delete_file"),
                                          {"file_pk": f.pk}).status_code, 404)
        self.assertTrue(VaultFile.objects.filter(pk=f.pk).exists())

    def test_a_clearance_that_keeps_a_bucket_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError):
            self.internal.delete()

    def test_one_clearance_keeps_a_bucket_once(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            BucketClearance.objects.create(bucket=self.bucket, clearance=self.internal)

    def test_deleting_the_bucket_takes_its_rows(self):
        self.bucket.delete()
        self.assertFalse(BucketClearance.objects.exists())


class BucketSectionTests(BucketClearanceTestCase):
    def page(self, bucket):
        return reverse("vault:bucket_metrics", args=[bucket.slug])

    def url(self, bucket):
        return reverse("vault:bucket_clearances", args=[bucket.slug])

    def test_a_superuser_sets_the_clearances_and_gives_them_back(self):
        confidential = Clearance.objects.create(name="confidential", slug="confidential")
        f = self.file(public=True, kept=False)
        self.client.force_login(self.root)
        body = self.client.get(self.page(self.open_bucket)).content.decode()
        self.assertIn('data-testid="bucket-clearance"', body)
        self.assertIn("confidential", body)                     # every clearance is offered
        response = self.client.post(self.url(self.open_bucket), {"clearance": [self.internal.pk]})
        self.assertRedirects(response, self.page(self.open_bucket) + "#clearances",
                             fetch_redirect_response=False)
        self.assertEqual([c.name for c in clearances.clearances_of(self.open_bucket)], ["internal"])
        self.assertFalse(may_read(self.stranger, f))
        self.assertFalse(may_read(self.owner, f))
        record = AuditRecord.objects.filter(action=ACTION).get()
        self.assertEqual(record.metadata["after"], ["internal"])
        self.assertEqual(record.metadata["slug"], "open")
        self.assertEqual(record.actor_user, self.root)
        self.client.post(self.url(self.open_bucket), {"clearance": [confidential.pk, "junk"]})
        self.assertEqual([c.name for c in clearances.clearances_of(self.open_bucket)], ["confidential"])
        self.client.post(self.url(self.open_bucket), {})
        self.assertEqual(clearances.clearances_of(self.open_bucket), [])
        self.assertTrue(may_read(self.stranger, f))
        self.assertTrue(AuditRecord.objects.filter(action=ACTION, metadata__open=True).exists())

    def test_saving_the_same_clearances_records_nothing(self):
        self.client.force_login(self.root)
        self.client.post(self.url(self.bucket), {"clearance": [self.internal.pk]})
        self.assertFalse(AuditRecord.objects.filter(action=ACTION).exists())

    def test_the_owner_sees_them_read_only_and_may_not_set_them(self):
        self.client.force_login(self.owner)
        body = self.client.get(self.page(self.bucket)).content.decode()
        self.assertIn('data-testid="bucket-clearance-list"', body)
        self.assertIn("internal", body)
        self.assertNotIn('data-testid="bucket-clearance"', body)
        self.assertEqual(self.client.post(self.url(self.bucket), {}).status_code, 403)
        self.assertEqual(self.client.post(self.url(self.open_bucket),
                                          {"clearance": [self.internal.pk]}).status_code, 403)
        self.assertEqual(clearances.clearances_of(self.bucket), [self.internal])
        self.assertEqual(clearances.clearances_of(self.open_bucket), [])
        self.assertFalse(AuditRecord.objects.filter(action=ACTION).exists())

    def test_the_owner_of_a_kept_bucket_sees_none_of_its_files_there(self):
        self.file(public=True, title="secret-deck.txt")
        self.client.force_login(self.owner)
        response = self.client.get(self.page(self.bucket))
        self.assertEqual(response.context["total_files"], 0)
        self.assertNotContains(response, "secret-deck.txt")
        self.client.force_login(self.root)
        self.assertEqual(self.client.get(self.page(self.bucket)).context["total_files"], 1)

    def test_anybody_else_gets_the_404_of_the_page(self):
        for user in (self.stranger, self.member, self.staff):
            self.client.force_login(user)
            self.assertEqual(self.client.post(self.url(self.bucket), {}).status_code, 404, user)
        self.assertEqual(clearances.clearances_of(self.bucket), [self.internal])

    def test_get_is_refused_and_a_visitor_is_sent_to_log_in(self):
        self.client.force_login(self.root)
        self.assertEqual(self.client.get(self.url(self.bucket)).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(self.url(self.bucket), {}).status_code, 302)
        self.assertEqual(clearances.clearances_of(self.bucket), [self.internal])

    def test_ids_are_ascii_digits_only(self):
        self.assertEqual(clearances._ids(["12", "abc", " 3", "-4", "1" * 19, "١", "²"]), {12})

    def test_may_manage_is_a_superuser_on_the_plan(self):
        from django.contrib.auth.models import AnonymousUser
        from toto.subscriptions.plans import admin_plan

        self.assertTrue(clearances.may_manage(self.root))
        others = [None, AnonymousUser(), self.owner, self.staff, self.member]
        if admin_plan() is not None:          # a ladder with a plan for admins
            others.append(User.objects.create_superuser("bare", "b@e.com", "pw"))
        for user in others:
            self.assertFalse(clearances.may_manage(user), user)
