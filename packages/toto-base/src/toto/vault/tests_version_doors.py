"""Who may READ a file's history and who may WRITE it (2026-09-30).

``version_views`` used to answer one question for all six doors — "may this
person work with the file?" — and answered it too widely: a folder with an
empty ACL let every signed-in account in, and a public file let every reader
take the lock, cut a version and restore an old body over its owner's work.

The doors are split now. Listing the history follows the vault's read rule
(``access.may_read``, bucket clearances first). Taking, beating and releasing
the lock, cutting a version and restoring one follow the write rule the
editors' save doors apply (``access.may_write``: the owner, the app that lends
the file out, or a superuser on the Superuser plan), and only after the read
gate. A file the caller may not see is 404; one they may see but not change is
403.
"""

import io
import json
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.audit.models import AuditRecord
from toto.people.models import Person
from toto.socialhub.models import Clearance
from toto.vault import access, locks, versions
from toto.vault.models import (Bucket, BucketClearance, FileLock, FileVersion, VaultDirectory,
                               VaultFile)
from toto.vault.plugins import VaultAccessPlugin

User = get_user_model()

WRITE_DOORS = ("lock_acquire", "lock_heartbeat", "lock_release", "version_save")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-version-doors-"))
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.member = User.objects.create_user("member", password="pw")
        cls.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        cls.root = User.objects.create_superuser("root", "r@e.com", "pw")
        call_command("bootstrap_plans", stdout=io.StringIO())      # root → the Superuser plan
        cls.root = User.objects.get(pk=cls.root.pk)
        # Made after the bootstrap: a superuser who never took the plan.
        cls.bare_root = User.objects.create_superuser("bareroot", "b@e.com", "pw")
        cls.bucket = Bucket.objects.create(name="Owned", slug="owned", owner=cls.owner)

    _n = 0

    def file(self, body=b"first", *, public=False, directory=None, bucket=None, owner=None):
        type(self)._n += 1
        vault_file = VaultFile(owner=owner or self.owner, title=f"doc{self._n}.txt",
                               key=f"doc-{self._n}", file_type="text", is_public=public,
                               directory=directory, bucket=bucket or self.bucket)
        vault_file.file.save(f"doc{self._n}.txt", ContentFile(body), save=False)
        vault_file.save()
        return vault_file

    def with_history(self, **kwargs):
        """A file at "second" with v1 = "first" to restore."""
        f = self.file(b"first", **kwargs)
        v1 = versions.save_version(f, author=self.owner)
        with f.file.open("wb") as handle:
            handle.write(b"second")
        return f, v1

    def body(self, vault_file):
        vault_file.refresh_from_db()
        with vault_file.file.open("rb") as handle:
            return handle.read()

    def u(self, name, vault_file, *extra):
        return reverse(f"vault:{name}", args=[vault_file.pk, *extra])

    def statuses(self, user, f, v1):
        """What every door answers ``user``, by url name."""
        self.client.force_login(user)
        out = {"version_list": self.client.get(self.u("version_list", f)).status_code}
        for name in WRITE_DOORS:
            out[name] = self.client.post(self.u(name, f)).status_code
        out["version_restore"] = self.client.post(
            self.u("version_restore", f, v1.pk)).status_code
        return out

    def assertUntouched(self, f):
        self.assertEqual(self.body(f), b"second")
        self.assertEqual(FileVersion.objects.filter(file=f).count(), 1)
        self.assertFalse(FileLock.objects.filter(file=f).exists())


class EmptyAclFolderTests(_Fixture):
    """A folder nobody put anyone on is not a folder open to everyone."""

    def setUp(self):
        self.inbox = VaultDirectory.objects.create(name="inbox", bucket=self.bucket,
                                                   owner=self.owner)

    def test_another_member_can_neither_see_lock_cut_nor_restore(self):
        f, v1 = self.with_history(directory=self.inbox)
        got = self.statuses(self.member, f, v1)
        self.assertEqual(set(got.values()), {404}, got)
        self.assertUntouched(f)

    def test_staff_is_no_one_special_there(self):
        f, v1 = self.with_history(directory=self.inbox)
        self.assertEqual(set(self.statuses(self.staff, f, v1).values()), {404})
        self.assertUntouched(f)

    def test_the_owner_still_can(self):
        f, v1 = self.with_history(directory=self.inbox)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.u("lock_acquire", f)).status_code, 200)
        self.assertEqual(self.client.post(self.u("version_save", f)).status_code, 200)
        response = self.client.post(self.u("version_restore", f, v1.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.body(f), b"first")


class PublicFileTests(_Fixture):
    """Public means anyone may read it, never that anyone may change it."""

    def test_a_reader_sees_the_history_and_is_told_it_is_read_only(self):
        f, _v1 = self.with_history(public=True)
        self.client.force_login(self.member)
        response = self.client.get(self.u("version_list", f))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([v["number"] for v in payload["versions"]], [1])
        self.assertFalse(payload["can_write"])

    def test_a_reader_is_refused_every_write_with_403(self):
        f, v1 = self.with_history(public=True)
        got = self.statuses(self.member, f, v1)
        self.assertEqual(got.pop("version_list"), 200)
        self.assertEqual(set(got.values()), {403}, got)
        self.assertUntouched(f)

    def test_the_refusal_says_why_and_that_the_caller_cannot_write(self):
        f, v1 = self.with_history(public=True)
        self.client.force_login(self.member)
        payload = self.client.post(self.u("version_restore", f, v1.pk)).json()
        self.assertFalse(payload["can_write"])
        self.assertIn("not change it", payload["error"])

    def test_a_reader_cannot_keep_someone_elses_lock_alive_or_free_it(self):
        f = self.file(public=True)
        locks.acquire(f, self.owner)
        before = locks.holder_of(f).expires_at
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(self.u("lock_heartbeat", f)).status_code, 403)
        self.assertEqual(self.client.post(self.u("lock_release", f)).status_code, 403)
        self.assertEqual(locks.holder_of(f).expires_at, before)
        self.assertEqual(locks.holder_of(f).holder, self.owner)

    def test_staff_read_a_public_file_like_anyone_and_write_it_like_anyone(self):
        f, v1 = self.with_history(public=True)
        got = self.statuses(self.staff, f, v1)
        self.assertEqual(got.pop("version_list"), 200)
        self.assertEqual(set(got.values()), {403}, got)
        self.assertUntouched(f)

    def test_the_owner_still_can_and_is_told_so(self):
        f, v1 = self.with_history(public=True)
        self.client.force_login(self.owner)
        self.assertTrue(self.client.get(self.u("version_list", f)).json()["can_write"])
        payload = self.client.post(self.u("lock_acquire", f)).json()
        self.assertTrue(payload["mine"])
        self.assertTrue(payload["can_write"])
        self.assertTrue(self.client.post(self.u("lock_heartbeat", f)).json()["held"])
        self.assertEqual(self.client.post(self.u("version_save", f)).status_code, 200)
        self.assertEqual(self.client.post(self.u("version_restore", f, v1.pk)).status_code,
                         200)
        self.assertEqual(self.body(f), b"first")
        self.assertEqual(self.client.post(self.u("lock_release", f)).json(),
                         {"released": True})


class ReadersWhoAreNotWritersTests(_Fixture):
    """Every other arm of ``may_read`` reads the history and writes nothing."""

    def test_a_folder_acl_member_reads_but_does_not_write(self):
        team = VaultDirectory.objects.create(name="team", bucket=self.bucket, owner=self.owner)
        team.allowed_users.add(self.member)
        f, v1 = self.with_history(directory=team)
        got = self.statuses(self.member, f, v1)
        self.assertEqual(got.pop("version_list"), 200)
        self.assertEqual(set(got.values()), {403}, got)
        self.assertUntouched(f)

    def test_the_buckets_owner_reads_another_persons_file_but_does_not_write(self):
        f, v1 = self.with_history(owner=self.member)
        got = self.statuses(self.owner, f, v1)       # owner of the bucket, not the file
        self.assertEqual(got.pop("version_list"), 200)
        self.assertEqual(set(got.values()), {403}, got)


class SuperuserTests(_Fixture):
    def test_a_superuser_on_the_plan_still_can(self):
        f, v1 = self.with_history()
        self.client.force_login(self.root)
        self.assertTrue(self.client.get(self.u("version_list", f)).json()["can_write"])
        self.assertTrue(self.client.post(self.u("lock_acquire", f)).json()["mine"])
        self.assertEqual(self.client.post(self.u("version_save", f)).status_code, 200)
        self.assertEqual(self.client.post(self.u("version_restore", f, v1.pk)).status_code,
                         200)
        self.assertEqual(self.body(f), b"first")

    def test_a_superuser_without_the_plan_reads_but_does_not_write(self):
        f, v1 = self.with_history()
        got = self.statuses(self.bare_root, f, v1)
        self.assertEqual(got.pop("version_list"), 200)
        self.assertEqual(set(got.values()), {403}, got)
        self.assertUntouched(f)

    def test_a_superuser_without_the_plan_still_writes_their_own_file(self):
        f, v1 = self.with_history(owner=self.bare_root)
        self.client.force_login(self.bare_root)
        self.assertEqual(self.client.post(self.u("version_restore", f, v1.pk)).status_code,
                         200)


class KeptBucketTests(_Fixture):
    """A bucket kept to a clearance hides the file — history, lock and all."""

    def setUp(self):
        self.clearance = Clearance.objects.create(name="payroll", slug="payroll")
        BucketClearance.objects.create(bucket=self.bucket, clearance=self.clearance)

    def hold(self, user):
        person, _ = Person.objects.get_or_create(user=user,
                                                 defaults={"display_name": user.username})
        person.clearances.add(self.clearance)

    def test_a_member_without_the_clearance_finds_nothing(self):
        f, v1 = self.with_history(public=True)
        self.assertEqual(set(self.statuses(self.member, f, v1).values()), {404})
        self.assertUntouched(f)

    def test_its_owner_without_the_clearance_finds_nothing_either(self):
        f, v1 = self.with_history()
        self.assertEqual(set(self.statuses(self.owner, f, v1).values()), {404})
        self.assertUntouched(f)

    def test_a_lending_app_does_not_open_a_kept_bucket(self):
        f, v1 = self.with_history()

        class Lender:
            def may_edit(self, user, vault_file):
                return True

        with patch.dict(VaultAccessPlugin.registry, {"text": Lender()}):
            self.assertEqual(set(self.statuses(self.member, f, v1).values()), {404})

    def test_a_holder_reads_and_only_the_owner_holding_it_writes(self):
        f, v1 = self.with_history()
        self.hold(self.member)
        got = self.statuses(self.member, f, v1)
        self.assertEqual(got.pop("version_list"), 200)
        self.assertEqual(set(got.values()), {403}, got)
        self.hold(self.owner)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.u("version_restore", f, v1.pk)).status_code,
                         200)

    def test_a_superuser_on_the_plan_passes_the_bucket(self):
        f, v1 = self.with_history()
        self.client.force_login(self.root)
        self.assertEqual(self.client.post(self.u("version_restore", f, v1.pk)).status_code,
                         200)


class LendingAppTests(_Fixture):
    """The app that lends a file out (cyprian's wiki) still decides its writers."""

    def test_a_lent_writer_who_cannot_read_it_otherwise_still_can(self):
        f, v1 = self.with_history()

        class Lender:
            def may_edit(self, user, vault_file):
                return user.username == "member"

        with patch.dict(VaultAccessPlugin.registry, {"text": Lender()}):
            self.client.force_login(self.member)
            self.assertTrue(self.client.get(self.u("version_list", f)).json()["can_write"])
            self.assertEqual(self.client.post(self.u("lock_acquire", f)).status_code, 200)
            self.assertEqual(self.client.post(self.u("version_restore", f, v1.pk)).status_code,
                             200)
            self.assertEqual(set(self.statuses(self.staff, f, v1).values()), {404})


class MayWriteTests(_Fixture):
    """``access.may_write`` itself, beside the rule it has to agree with."""

    def test_the_arms(self):
        f = self.file(public=True)
        self.assertTrue(access.may_write(self.owner, f))
        self.assertTrue(access.may_write(self.root, f))
        for user in (self.member, self.staff, self.bare_root):
            self.assertFalse(access.may_write(user, f), user)
        self.assertFalse(access.may_write(None, f))
        self.assertFalse(access.may_write(self.owner, None))

    def test_a_writer_the_vault_would_otherwise_refuse_is_one_may_read_refuses_too(self):
        # The write rule never widens reading beyond the lending app: every
        # other writer is somebody may_read already lets in.
        f = self.file()
        for user in (self.owner, self.root):
            self.assertTrue(access.may_read(user, f))

    def test_a_broken_lending_app_refuses(self):
        f = self.file()

        class Broken:
            def may_edit(self, user, vault_file):
                raise RuntimeError("down")

        with patch.dict(VaultAccessPlugin.registry, {"text": Broken()}):
            self.assertFalse(access.may_write(self.member, f))


class ChainTests(_Fixture):
    """A refused write is on the audit chain, as the door's other outcomes are."""

    def rows(self, action):
        return AuditRecord.objects.filter(app_label="vault", action=action)

    def test_a_refused_restore_is_recorded_as_a_failure_without_the_body(self):
        f, v1 = self.with_history(public=True)
        self.client.force_login(self.member)
        self.client.post(self.u("version_restore", f, v1.pk))
        entry = self.rows("FILE_RESTORED").get()
        self.assertFalse(entry.success)
        self.assertEqual(entry.metadata["status"], 403)
        self.assertEqual(entry.actor_user, self.member)
        self.assertNotIn("first", json.dumps(entry.metadata))

    def test_a_refused_lock_claim_is_recorded_once(self):
        f = self.file(public=True)
        self.client.force_login(self.member)
        self.client.post(self.u("lock_acquire", f))
        entry = self.rows("FILE_LOCK_REFUSED").get()
        self.assertFalse(entry.success)
        self.assertEqual((entry.object_id, entry.metadata["status"]), (str(f.pk), 403))

    def test_a_claim_on_a_file_somebody_is_editing_is_not_a_refusal(self):
        # 423 is contention between two writers, not a security refusal: it
        # would otherwise flood the chain every time two people open a file.
        f = self.file()
        locks.acquire(f, self.root)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.u("lock_acquire", f)).status_code, 423)
        self.assertFalse(self.rows("FILE_LOCK_REFUSED").exists())

    def test_a_granted_lock_and_heartbeats_stay_off_the_chain(self):
        f = self.file()
        self.client.force_login(self.owner)
        self.client.post(self.u("lock_acquire", f))
        self.client.post(self.u("lock_heartbeat", f))
        self.client.post(self.u("lock_release", f))
        self.assertFalse(AuditRecord.objects.filter(app_label="vault",
                                                    object_id=str(f.pk)).exists())
