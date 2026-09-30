"""Several files at once from the file list (``bulk.py``, 2026-10-01): "Move
to the trash" and "Move to…" check each file by its single door's rules and
answer per file — done or refused with the reason — never all or nothing;
one audit record per file; a mounted remote bucket's file is deleted at once
only when the request says so; a move into another bucket needs a bucket the
member may write, on this server's disk.

    manage.py test toto.vault.tests_bulk
"""

import json
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from toto.audit.models import AuditRecord
from toto.people.models import Person
from toto.socialhub.models import Clearance
from toto.vault import bulk
from toto.vault.models import Bucket, BucketClearance, FileOrigin, VaultDirectory, VaultFile

User = get_user_model()


def records(action=None):
    rows = AuditRecord.objects.filter(app_label="vault").order_by("sequence")
    if action:
        rows = rows.filter(action=action)
    return list(rows)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-bulk-"))
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform

        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.other = User.objects.create_user("other", password="pw")
        cls.bucket = Bucket.objects.create(name="Owned", slug="owned", owner=cls.owner)
        cls.folder = VaultDirectory.objects.create(name="folder", bucket=cls.bucket,
                                                   owner=cls.owner)
        cls.second = Bucket.objects.create(name="Second", slug="second", owner=cls.owner)
        cls.inbox = VaultDirectory.objects.create(name="inbox", bucket=cls.second,
                                                  owner=cls.owner)
        cls.payroll = Clearance.objects.create(name="payroll", slug="payroll")
        cls.kept = Bucket.objects.create(name="Kept", slug="kept", owner=cls.owner)
        BucketClearance.objects.create(bucket=cls.kept, clearance=cls.payroll)

    def file(self, key, *, owner=None, bucket=None, directory="folder"):
        vault_file = VaultFile(owner=owner or self.owner, title=f"{key}.txt", key=key,
                               file_type="text", bucket=bucket or self.bucket,
                               directory=self.folder if directory == "folder" else directory)
        vault_file.file.save(f"{key}.txt", ContentFile(b"hello"), save=False)
        vault_file.save()
        return vault_file

    def post(self, name, files, user=None, **data):
        client = Client()
        client.force_login(user or self.owner)
        response = client.post(reverse(f"vault:{name}"),
                               {"files": [str(getattr(f, "pk", f)) for f in files], **data})
        return response, (json.loads(response.content) if response.content else {})

    def statuses(self, body):
        return {r["id"]: r["status"] for r in body["results"]}


class BulkTrashTests(_Fixture):
    def test_each_file_goes_to_the_trash_with_one_record(self):
        a, b = self.file("a"), self.file("b")
        response, body = self.post("bulk_trash", [a, b])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.statuses(body), {a.pk: "trashed", b.pk: "trashed"})
        self.assertEqual((body["done"], body["refused"]), (2, 0))
        self.assertEqual(VaultFile.objects.filter(pk__in=[a.pk, b.pk]).count(), 0)
        self.assertEqual(VaultFile.all_objects.filter(pk__in=[a.pk, b.pk],
                                                      trashed_at__isnull=False).count(), 2)
        self.assertEqual([r.action for r in records()], ["FILE_TRASHED", "FILE_TRASHED"])

    def test_another_member_s_file_is_refused_and_the_rest_goes(self):
        mine, theirs = self.file("mine"), self.file("theirs", owner=self.other)
        _, body = self.post("bulk_trash", [mine, theirs])
        self.assertEqual(self.statuses(body), {mine.pk: "trashed", theirs.pk: "refused"})
        self.assertTrue(VaultFile.objects.filter(pk=theirs.pk).exists())
        refused = [r for r in records() if not r.success]
        self.assertEqual([(r.object_id, r.metadata["refused"]) for r in refused],
                         [(str(theirs.pk), "not_found")])

    def test_an_owner_lacking_the_bucket_s_clearance_is_refused(self):
        secret = self.file("secret", bucket=self.kept, directory=None)
        _, body = self.post("bulk_trash", [secret])
        self.assertEqual(self.statuses(body), {secret.pk: "refused"})
        self.assertTrue(VaultFile.objects.filter(pk=secret.pk).exists())

    def test_a_holder_of_the_clearance_trashes(self):
        Person.objects.create(user=self.owner, display_name="O").clearances.add(self.payroll)
        secret = self.file("secret", bucket=self.kept, directory=None)
        _, body = self.post("bulk_trash", [secret])
        self.assertEqual(self.statuses(body), {secret.pk: "trashed"})

    def test_a_mirror_row_is_refused(self):
        stub = self.file("stub")
        VaultFile.objects.filter(pk=stub.pk).update(origin=FileOrigin.MIRROR)
        _, body = self.post("bulk_trash", [stub])
        self.assertEqual(self.statuses(body), {stub.pk: "refused"})

    def test_a_remote_mount_s_file_waits_for_the_confirmation(self):
        mounted = Bucket.objects.create(name="Mounted", slug="mounted", owner=self.owner,
                                        storage_backend="remote_toto")
        remote = VaultFile.objects.create(owner=self.owner, title="r.txt", key="r",
                                          file_type="text", bucket=mounted)
        _, body = self.post("bulk_trash", [remote])
        self.assertEqual(self.statuses(body), {remote.pk: "refused"})
        self.assertTrue(VaultFile.objects.filter(pk=remote.pk).exists())
        _, body = self.post("bulk_trash", [remote], remote="yes")
        self.assertEqual(self.statuses(body), {remote.pk: "deleted"})
        self.assertFalse(VaultFile.all_objects.filter(pk=remote.pk).exists())
        self.assertEqual(records()[-1].action, "FILE_DELETED")

    def test_nothing_named_or_garbage_is_a_400(self):
        self.assertEqual(self.post("bulk_trash", [])[0].status_code, 400)
        self.assertEqual(self.post("bulk_trash", ["x1"])[0].status_code, 400)

    def test_too_many_is_a_400(self):
        with mock.patch.object(bulk, "MAX_FILES", 2):
            self.assertEqual(self.post("bulk_trash", [1, 2, 3])[0].status_code, 400)

    def test_a_visitor_and_a_get_are_turned_away(self):
        self.assertEqual(Client().post(reverse("vault:bulk_trash"), {"files": ["1"]})
                         .status_code, 302)
        client = Client()
        client.force_login(self.owner)
        self.assertEqual(client.get(reverse("vault:bulk_trash")).status_code, 405)

    def test_the_door_wants_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        a = self.file("a")
        self.assertEqual(client.post(reverse("vault:bulk_trash"), {"files": [a.pk]})
                         .status_code, 403)
        self.assertTrue(VaultFile.objects.filter(pk=a.pk).exists())


class BulkMoveTests(_Fixture):
    def test_within_the_bucket_to_the_root_and_back(self):
        a, b = self.file("a"), self.file("b")
        _, body = self.post("bulk_move", [a, b], destination_bucket=self.bucket.pk)
        self.assertEqual(self.statuses(body), {a.pk: "moved", b.pk: "moved"})
        self.assertEqual(set(VaultFile.objects.filter(pk__in=[a.pk, b.pk])
                             .values_list("directory_id", flat=True)), {None})
        self.assertEqual([r.action for r in records()], ["FILE_MOVED", "FILE_MOVED"])
        _, body = self.post("bulk_move", [a], destination_bucket=self.bucket.pk,
                            destination_directory=self.folder.pk)
        self.assertEqual(body["results"][0]["pid"], self.folder.pk)

    def test_a_file_already_there_is_unchanged_and_unrecorded(self):
        a = self.file("a")
        _, body = self.post("bulk_move", [a], destination_bucket=self.bucket.pk,
                            destination_directory=self.folder.pk)
        self.assertEqual(self.statuses(body), {a.pk: "unchanged"})
        self.assertEqual(records(), [])

    def test_into_another_bucket_of_mine(self):
        a = self.file("a")
        clash = self.file("a", bucket=self.second, directory=None)
        _, body = self.post("bulk_move", [a], destination_bucket=self.second.pk,
                            destination_directory=self.inbox.pk)
        self.assertEqual(self.statuses(body), {a.pk: "moved"})
        a.refresh_from_db()
        self.assertEqual((a.bucket_id, a.directory_id, a.title),
                         (self.second.pk, self.inbox.pk, "a.txt"))
        self.assertNotEqual(a.key, clash.key)          # the key was taken there
        entry = records("FILE_MOVED")[0]
        self.assertEqual((entry.metadata["bucket_id"], entry.metadata["from_bucket_id"]),
                         (self.second.pk, self.bucket.pk))
        self.assertNotIn("a.txt", str(entry.metadata))

    def test_into_another_member_s_bucket_is_refused_per_file(self):
        theirs = Bucket.objects.create(name="Theirs", slug="theirs", owner=self.other)
        a = self.file("a")
        _, body = self.post("bulk_move", [a], destination_bucket=theirs.pk)
        self.assertEqual(self.statuses(body), {a.pk: "refused"})
        a.refresh_from_db()
        self.assertEqual(a.bucket_id, self.bucket.pk)
        self.assertEqual(records()[0].metadata["refused"], "not_writable")

    def test_a_bucket_being_deleted_takes_nothing(self):
        from django.utils import timezone

        Bucket.objects.filter(pk=self.second.pk).update(deletion_requested_at=timezone.now())
        a = self.file("a")
        _, body = self.post("bulk_move", [a], destination_bucket=self.second.pk)
        self.assertEqual(self.statuses(body), {a.pk: "refused"})

    def test_between_storage_kinds_is_refused(self):
        s3 = Bucket.objects.create(name="S3", slug="s3b", owner=self.owner,
                                   storage_backend="s3")
        remote = VaultFile.objects.create(owner=self.owner, title="r.txt", key="r",
                                          file_type="text", bucket=s3)
        local = self.file("a")
        _, body = self.post("bulk_move", [remote, local], destination_bucket=self.second.pk)
        self.assertEqual(self.statuses(body), {remote.pk: "refused", local.pk: "moved"})
        _, body = self.post("bulk_move", [local], destination_bucket=s3.pk)
        self.assertEqual(self.statuses(body), {local.pk: "refused"})

    def test_a_kept_destination_is_not_named_to_an_outsider(self):
        a = self.file("a")
        response, body = self.post("bulk_move", [a], destination_bucket=self.kept.pk)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("Kept", body["error"])
        # A request refused whole is the middleware's one refusal line.
        self.assertEqual([(r.action, r.success) for r in records()], [("FILE_MOVED", False)])

    def test_a_folder_of_another_bucket_is_a_400(self):
        a = self.file("a")
        response, _ = self.post("bulk_move", [a], destination_bucket=self.bucket.pk,
                                destination_directory=self.inbox.pk)
        self.assertEqual(response.status_code, 400)
        a.refresh_from_db()
        self.assertEqual(a.directory_id, self.folder.pk)

    def test_another_member_s_file_is_refused_and_the_rest_moves(self):
        mine, theirs = self.file("mine"), self.file("theirs", owner=self.other)
        _, body = self.post("bulk_move", [mine, theirs], destination_bucket=self.bucket.pk)
        self.assertEqual(self.statuses(body), {mine.pk: "moved", theirs.pk: "refused"})
        self.assertEqual([(r.action, r.success) for r in records()],
                         [("FILE_MOVED", True), ("FILE_MOVED", False)])

    def test_a_trashed_file_does_not_move(self):
        a = self.file("a")
        a.trash(self.owner)
        _, body = self.post("bulk_move", [a], destination_bucket=self.second.pk)
        self.assertEqual(self.statuses(body), {a.pk: "refused"})


class MoveTargetsTests(_Fixture):
    def test_my_buckets_and_where_my_files_are(self):
        theirs = Bucket.objects.create(name="Theirs", slug="theirs", owner=self.other)
        self.file("a", bucket=theirs, directory=None)
        targets = {t["id"]: t for t in bulk.move_targets(self.owner)}
        self.assertEqual(set(targets), {self.bucket.pk, self.second.pk, theirs.pk})
        self.assertTrue(targets[self.second.pk]["into"])
        self.assertFalse(targets[theirs.pk]["into"])
        self.assertEqual(targets[self.second.pk]["dirs"], [{"id": self.inbox.pk,
                                                            "path": "inbox"}])

    def test_the_file_list_carries_them(self):
        client = Client()
        client.force_login(self.owner)
        response = client.get(reverse("vault:public_list"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("vault-move-targets", response.content.decode())
        self.assertIn(reverse("vault:bulk_move"), response.content.decode())
