"""The HTTP surface of versions and editing locks (``version_views``).

tests_versions and tests_locks pin the services; nothing pinned the doors
the three editors actually call — who ``_file_for`` lets through, what each
endpoint answers when somebody else holds the lock, and the remote-bytes
refusal. Who may read the history and who may write it is
``tests_version_doors`` (2026-09-30).
"""

import io
import json
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.people.models import Person
from toto.socialhub.models import Clearance
from toto.vault import locks, versions
from toto.vault.models import (Bucket, BucketClearance, FileLock, FileVersion, VaultDirectory,
                               VaultFile)
from toto.vault.plugins import VaultAccessPlugin
from toto.vault.version_views import _file_for

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-more-version-views-"))
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.other = User.objects.create_user("other", password="pw")
        cls.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        cls.root = User.objects.create_superuser("root", "r@e.com", "pw")
        # root → the Superuser plan: the second WRITER these tests need is a
        # superuser on it (2026-09-30). Staff is a reader like anyone else.
        call_command("bootstrap_plans", stdout=io.StringIO())
        cls.root = User.objects.get(pk=cls.root.pk)
        cls.bucket = Bucket.objects.create(name="Owned", slug="owned", owner=cls.owner)

    _n = 0

    def file(self, body=b"v1 body", *, public=False, directory=None, bucket="default"):
        type(self)._n += 1
        vault_file = VaultFile(owner=self.owner, title=f"doc{self._n}.txt", key=f"doc-{self._n}",
                               file_type="text", is_public=public, directory=directory,
                               bucket=self.bucket if bucket == "default" else bucket)
        vault_file.file.save(f"doc{self._n}.txt", ContentFile(body), save=False)
        vault_file.save()
        return vault_file

    def body(self, vault_file):
        vault_file.refresh_from_db()
        with vault_file.file.open("rb") as handle:
            return handle.read()

    def write(self, vault_file, body):
        with vault_file.file.open("wb") as handle:
            handle.write(body)

    def u(self, name, vault_file, *extra):
        return reverse(f"vault:{name}", args=[vault_file.pk, *extra])


class FileForTests(_Fixture):
    def request(self, user):
        request = RequestFactory().get("/")
        request.user = user
        return request

    def test_a_missing_file_is_404(self):
        with self.assertRaises(Http404):
            _file_for(self.request(self.owner), 424242)

    def test_the_owner_and_a_superuser_reach_a_private_file_and_staff_do_not(self):
        # Staff used to reach every file here (2026-09-30): the download door
        # never let them, so the history said more than the file did.
        f = self.file()
        for user in (self.owner, self.root):
            self.assertEqual(_file_for(self.request(user), f.pk), f)
        with self.assertRaises(Http404):
            _file_for(self.request(self.staff), f.pk)

    def test_a_stranger_does_not_reach_a_private_bucket_root_file(self):
        with self.assertRaises(Http404):
            _file_for(self.request(self.other), self.file().pk)

    def test_a_folder_acl_member_reaches_it(self):
        directory = VaultDirectory.objects.create(name="team", bucket=self.bucket,
                                                  owner=self.owner)
        directory.allowed_users.add(self.other)
        self.assertTrue(_file_for(self.request(self.other), self.file(directory=directory).pk))

    def test_a_folder_acl_that_names_someone_else_keeps_a_stranger_out(self):
        directory = VaultDirectory.objects.create(name="team", bucket=self.bucket,
                                                  owner=self.owner)
        directory.allowed_users.add(self.staff)
        with self.assertRaises(Http404):
            _file_for(self.request(self.other), self.file(directory=directory).pk)

    def test_a_lending_app_opens_it(self):
        f = self.file()

        class Lender:
            def may_edit(self, user, vault_file):
                return user.username == "other"

        with patch.dict(VaultAccessPlugin.registry, {"text": Lender()}):
            self.assertEqual(_file_for(self.request(self.other), f.pk), f)

    def test_a_kept_bucket_closes_it_even_to_its_owner_staff_and_lending_apps(self):
        clearance = Clearance.objects.create(name="internal", slug="internal")
        f = self.file(public=True)
        BucketClearance.objects.create(bucket=f.bucket, clearance=clearance)

        class Lender:
            def may_edit(self, user, vault_file):
                return True

        with patch.dict(VaultAccessPlugin.registry, {"text": Lender()}):
            for user in (self.staff, self.other, self.owner):
                with self.assertRaises(Http404):
                    _file_for(self.request(user), f.pk)
        Person.objects.create(user=self.other, display_name="O").clearances.add(clearance)
        self.assertEqual(_file_for(self.request(self.other), f.pk), f)


    def test_the_history_and_the_lock_are_missing_to_an_owner_holding_nothing(self):
        clearance = Clearance.objects.create(name="internal", slug="internal")
        f = self.file()
        BucketClearance.objects.create(bucket=f.bucket, clearance=clearance)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("vault:version_list", args=[f.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("vault:lock_acquire", args=[f.pk])).status_code, 404)
        self.assertFalse(FileLock.objects.filter(file=f).exists())
        Person.objects.create(user=self.owner, display_name="Ow").clearances.add(clearance)
        self.assertEqual(self.client.get(reverse("vault:version_list", args=[f.pk])).status_code, 200)


class LockEndpointTests(_Fixture):
    def test_every_door_wants_a_login(self):
        f = self.file()
        for name in ("lock_acquire", "lock_heartbeat", "lock_release", "version_list",
                     "version_save"):
            response = self.client.post(self.u(name, f))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn("login", response["Location"])

    def test_the_lock_doors_refuse_a_get(self):
        f = self.file()
        self.client.force_login(self.owner)
        for name in ("lock_acquire", "lock_heartbeat", "lock_release", "version_save"):
            self.assertEqual(self.client.get(self.u(name, f)).status_code, 405, name)

    def test_acquiring_reports_that_it_is_mine(self):
        f = self.file()
        self.client.force_login(self.owner)
        payload = self.client.post(self.u("lock_acquire", f)).json()
        self.assertTrue(payload["locked"])
        self.assertTrue(payload["mine"])
        self.assertEqual(payload["holder"], "owner")
        self.assertEqual(payload["heartbeat_seconds"], locks.HEARTBEAT_SECONDS)

    def test_a_second_writer_gets_423_and_the_holders_name(self):
        f = self.file(public=True)
        locks.acquire(f, self.owner)
        self.client.force_login(self.root)
        response = self.client.post(self.u("lock_acquire", f))
        self.assertEqual(response.status_code, 423)
        payload = response.json()
        self.assertIn("owner is editing this document.", payload["error"])
        self.assertFalse(payload["mine"])
        self.assertEqual(payload["holder"], "owner")

    def test_a_heartbeat_from_the_holder_holds_and_from_anyone_else_does_not(self):
        f = self.file(public=True)
        locks.acquire(f, self.owner)
        self.client.force_login(self.owner)
        self.assertTrue(self.client.post(self.u("lock_heartbeat", f)).json()["held"])
        self.client.force_login(self.root)
        payload = self.client.post(self.u("lock_heartbeat", f)).json()
        self.assertFalse(payload["held"])
        self.assertTrue(payload["locked"])

    def test_release_answers_200_even_when_nothing_was_held_and_frees_only_mine(self):
        f = self.file(public=True)
        locks.acquire(f, self.owner)
        self.client.force_login(self.root)
        self.assertEqual(self.client.post(self.u("lock_release", f)).json(), {"released": True})
        self.assertIsNotNone(locks.holder_of(f))
        self.client.force_login(self.owner)
        self.client.post(self.u("lock_release", f))
        self.assertIsNone(locks.holder_of(f))

    def test_an_expired_lock_reads_as_unlocked(self):
        f = self.file()
        FileLock.objects.create(file=f, holder=self.other,
                                expires_at=timezone.now() - timedelta(seconds=1))
        self.client.force_login(self.owner)
        payload = self.client.get(self.u("version_list", f)).json()
        self.assertFalse(payload["locked"])
        self.assertEqual(payload["holder"], "")

    def test_a_stranger_gets_404_not_a_lock(self):
        f = self.file()
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.u("lock_acquire", f)).status_code, 404)
        self.assertFalse(FileLock.objects.exists())


class VersionEndpointTests(_Fixture):
    def test_the_list_is_newest_first_with_what_the_editor_shows(self):
        f = self.file()
        versions.save_version(f, author=self.owner, label="first")
        self.write(f, b"v2 body")
        versions.save_version(f, author=None)
        self.client.force_login(self.owner)
        payload = self.client.get(self.u("version_list", f)).json()
        self.assertEqual([v["number"] for v in payload["versions"]], [2, 1])
        newest, oldest = payload["versions"]
        self.assertEqual(oldest["label"], "first")
        self.assertEqual(oldest["author"], "owner")
        self.assertEqual(newest["author"], "")
        self.assertEqual(oldest["size_bytes"], len(b"v1 body"))
        self.assertFalse(oldest["is_conflict"])
        self.assertTrue(oldest["pinned"])             # a named version is kept
        self.assertFalse(payload["locked"])

    def test_saving_takes_the_label_from_a_json_body(self):
        f = self.file()
        self.client.force_login(self.owner)
        response = self.client.post(self.u("version_save", f),
                                    data=json.dumps({"label": "  final draft  "}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["saved"])
        self.assertEqual(payload["label"], "final draft")
        self.assertEqual(payload["number"], 1)

    def test_saving_takes_the_label_from_a_form_post(self):
        f = self.file()
        self.client.force_login(self.owner)
        payload = self.client.post(self.u("version_save", f), {"label": "from a form"}).json()
        self.assertEqual(payload["label"], "from a form")

    def test_a_body_that_is_not_json_saves_unnamed_rather_than_failing(self):
        f = self.file()
        self.client.force_login(self.owner)
        response = self.client.post(self.u("version_save", f), data=b"{not json",
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["label"], "")

    def test_saving_while_somebody_else_holds_the_lock_is_423_and_writes_nothing(self):
        f = self.file(public=True)
        locks.acquire(f, self.staff)
        self.client.force_login(self.owner)
        response = self.client.post(self.u("version_save", f))
        self.assertEqual(response.status_code, 423)
        self.assertIn("staff is editing this document.", response.json()["error"])
        self.assertFalse(FileVersion.objects.exists())

    def test_the_holder_may_save_under_their_own_lock(self):
        f = self.file()
        locks.acquire(f, self.owner)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.u("version_save", f)).status_code, 200)

    def test_remote_bytes_are_refused_for_saving_and_restoring(self):
        s3 = Bucket.objects.create(name="S3", slug="s3", owner=self.owner, storage_backend="s3")
        f = self.file()
        version = versions.save_version(f, author=self.owner)
        VaultFile.objects.filter(pk=f.pk).update(bucket=s3)
        self.client.force_login(self.owner)
        response = self.client.post(self.u("version_save", f))
        self.assertEqual(response.status_code, 403)
        self.assertIn("remote storage", response.json()["error"])
        response = self.client.post(self.u("version_restore", f, version.pk))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(FileVersion.objects.filter(file=f).count(), 1)

    def test_restoring_writes_the_old_body_back_as_a_new_version(self):
        f = self.file(b"first")
        v1 = versions.save_version(f, author=self.owner)
        self.write(f, b"second")
        versions.save_version(f, author=self.owner)
        self.client.force_login(self.owner)
        response = self.client.post(self.u("version_restore", f, v1.pk))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["restored"])
        self.assertEqual(payload["from"], 1)
        self.assertEqual(payload["number"], 3)
        self.assertEqual(self.body(f), b"first")

    def test_a_version_of_another_file_cannot_be_restored_onto_this_one(self):
        mine, other = self.file(b"mine"), self.file(b"other")
        foreign = versions.save_version(other, author=self.owner)
        self.client.force_login(self.owner)
        response = self.client.post(self.u("version_restore", mine, foreign.pk))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.body(mine), b"mine")

    def test_restoring_under_somebody_elses_lock_is_423_and_changes_nothing(self):
        f = self.file(b"first", public=True)
        v1 = versions.save_version(f, author=self.owner)
        self.write(f, b"second")
        locks.acquire(f, self.staff)
        self.client.force_login(self.owner)
        response = self.client.post(self.u("version_restore", f, v1.pk))
        self.assertEqual(response.status_code, 423)
        self.assertEqual(self.body(f), b"second")


class FileForReachesTooFarTests(_Fixture):
    """The two holes ``_file_for`` had until 2026-09-30, when these were
    skipped as known bugs: ``directory.user_can_access()`` is True for a folder
    with an EMPTY ACL, so any signed-in account reached another person's
    private file there; and a PUBLIC file was returned to every reader, while
    the write doors checked only the lock. Reading follows ``may_read`` now and
    writing ``may_write`` (``tests_version_doors`` has the rest)."""

    def test_a_stranger_cannot_restore_a_private_file_in_a_folder_without_an_acl(self):
        directory = VaultDirectory.objects.create(name="inbox", bucket=self.bucket,
                                                  owner=self.owner)
        f = self.file(b"first", directory=directory)
        v1 = versions.save_version(f, author=self.owner)
        self.write(f, b"second")
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.u("version_list", f)).status_code, 404)
        self.assertEqual(self.client.post(self.u("version_restore", f, v1.pk)).status_code, 404)
        self.assertEqual(self.body(f), b"second")

    def test_a_reader_of_a_public_file_cannot_rewrite_it(self):
        f = self.file(b"first", public=True)
        v1 = versions.save_version(f, author=self.owner)
        self.write(f, b"second")
        self.client.force_login(self.other)
        response = self.client.post(self.u("version_restore", f, v1.pk))
        # They may read it, so it is not hidden from them: 403, not 404.
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.body(f), b"second")
