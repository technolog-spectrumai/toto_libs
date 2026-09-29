"""A file kept to circles (2026-09-29): read by their members, its owner and
superusers, and by nobody else — not through the public flag, not through a
folder's ACL, not through the bucket. Every vault door asks the one rule."""

import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db.models import ProtectedError
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from toto.audit.models import AuditRecord
from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Community
from toto.vault import circles
from toto.vault.access import may_read
from toto.vault.filetree import accessible_files
from toto.vault.models import Bucket, VaultDirectory, VaultFile, VaultFileCircle
from toto.vault.version_views import _file_for

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-circles-"))
class FileCircleTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.board = Community.objects.create(name="board", slug="board", is_circle=True)
        cls.devs = Community.objects.create(name="devs", slug="devs")
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.member = User.objects.create_user("member", password="pw")
        Person.objects.create(user=cls.member, display_name="Member").communities.add(cls.board)
        cls.stranger = User.objects.create_user("stranger", password="pw")
        Person.objects.create(user=cls.stranger, display_name="Stranger").communities.add(cls.devs)
        cls.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        cls.root = User.objects.create_superuser("root", "r@e.com", "pw")
        cls.bucket = Bucket.objects.create(name="Owned", slug="owned", owner=cls.owner)

    _n = 0

    def file(self, *, public=False, circle=True, directory=None, title="deck.txt"):
        type(self)._n += 1
        vault_file = VaultFile(owner=self.owner, title=title, key=f"f-{self._n}",
                               file_type="text", is_public=public, bucket=self.bucket,
                               directory=directory)
        vault_file.file.save(title, ContentFile(b"the contents"), save=False)
        vault_file.save()
        if circle:
            VaultFileCircle.objects.create(file=vault_file, circle=self.board)
        return vault_file


class RuleTests(FileCircleTestCase):
    def test_the_circle_its_owner_and_a_superuser_read_it(self):
        f = self.file()
        for user in (self.member, self.owner, self.root):
            self.assertTrue(may_read(user, f), user)

    def test_a_stranger_does_not_even_when_public(self):
        f = self.file(public=True)
        self.assertFalse(may_read(self.stranger, f))
        self.assertFalse(may_read(self.staff, f))
        self.assertFalse(may_read(None, f))                     # anonymous

    def test_a_functional_community_grants_nothing(self):
        self.assertFalse(may_read(self.stranger, self.file()))  # in devs only

    def test_a_folder_acl_and_the_bucket_no_longer_open_it(self):
        directory = VaultDirectory.objects.create(name="d", bucket=self.bucket, owner=self.owner)
        directory.allowed_users.add(self.stranger)
        self.assertFalse(may_read(self.stranger, self.file(directory=directory)))
        other_bucket = Bucket.objects.create(name="S", slug="s", owner=self.stranger)
        f = self.file()
        f.bucket = other_bucket
        f.save()
        self.assertFalse(may_read(self.stranger, f))

    def test_no_circle_is_the_vault_as_it_was(self):
        self.assertTrue(may_read(self.stranger, self.file(public=True, circle=False)))
        self.assertFalse(may_read(self.stranger, self.file(circle=False)))

    def test_the_queryset_agrees(self):
        kept, open_ = self.file(public=True), self.file(public=True, circle=False, title="open.txt")
        self.assertEqual(set(accessible_files(self.stranger)), {open_})
        self.assertEqual(set(accessible_files(self.member)), {kept, open_})
        self.assertEqual(set(accessible_files(self.root)), {kept, open_})

    def test_the_versions_door_and_the_browser(self):
        f = self.file(public=True)
        request = RequestFactory().get("/")
        request.user = self.staff
        with self.assertRaises(Http404):
            _file_for(request, f.pk)
        request.user = self.member
        self.assertEqual(_file_for(request, f.pk), f)
        self.client.force_login(self.stranger)
        body = self.client.get(reverse("vault:public_list")).content.decode()
        self.assertNotIn("deck.txt", body)
        self.client.force_login(self.member)
        self.assertIn("deck.txt", self.client.get(reverse("vault:public_list")).content.decode())

    def test_a_circle_that_keeps_a_file_cannot_be_deleted(self):
        self.file()
        with self.assertRaises(ProtectedError):
            self.board.delete()


class AccessPageTests(FileCircleTestCase):
    def url(self, f):
        return reverse("vault:file_access", args=[f.pk])

    def test_the_owner_keeps_the_file_to_circles_and_gives_it_back(self):
        f = self.file(circle=False)
        Person.objects.create(user=self.owner, display_name="Owner").communities.add(self.board)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url(f)), 'data-testid="access-circle"')
        self.client.post(self.url(f), {"circle": [self.board.pk], "next": "/sheets/"})
        self.assertEqual([c.name for c in circles.circles_of(f)], ["board"])
        self.assertFalse(may_read(self.stranger, f))
        record = AuditRecord.objects.filter(action="VAULT.FILE.CIRCLES_CHANGED").get()
        self.assertEqual(record.metadata["after"], ["board"])
        self.assertEqual(record.actor_user, self.owner)
        self.client.post(self.url(f), {})
        self.assertEqual(circles.circles_of(f), [])
        self.assertTrue(AuditRecord.objects.filter(action="VAULT.FILE.CIRCLES_CHANGED",
                                                   metadata__open=True).exists())

    def test_an_owner_is_offered_only_their_own_circles(self):
        f = self.file(circle=False)
        Community.objects.create(name="seniors", slug="seniors", is_circle=True)
        Person.objects.create(user=self.owner, display_name="Owner").communities.add(self.board)
        self.client.force_login(self.owner)
        body = self.client.get(self.url(f)).content.decode()
        self.assertIn("board", body)
        self.assertNotIn("seniors", body)
        self.client.force_login(self.root)
        self.assertIn("seniors", self.client.get(self.url(f)).content.decode())

    def test_a_functional_community_cannot_be_ticked(self):
        f = self.file(circle=False)
        self.client.force_login(self.root)
        self.client.post(self.url(f), {"circle": [self.devs.pk]})
        self.assertEqual(circles.circles_of(f), [])

    def test_who_may_not(self):
        f = self.file(public=True)
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.url(f)).status_code, 404)      # hidden is missing
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.url(f)).status_code, 403)      # reads, does not decide
