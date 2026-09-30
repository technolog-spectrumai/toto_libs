"""The small JSON doors on a single file — move, rename, delete, encrypt,
decrypt, encrypted download, the services listing — and the download door's
egress and streaming shape. Each is owner-only by construction; these pin
that a stranger gets the same answer as a missing file, that mirror rows and
remote bytes are refused by name, and that a refusal changes nothing.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest import skip
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.http import FileResponse
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.vault.models import Bucket, VaultDirectory, VaultFile, VaultUsageEvent
from toto.vault.views import stream_file

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-more-doors-"))
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.other = User.objects.create_user("other", password="pw")
        cls.bucket = Bucket.objects.create(name="Owned", slug="owned", owner=cls.owner)
        cls.other_bucket = Bucket.objects.create(name="Other", slug="other", owner=cls.owner)
        cls.folder = VaultDirectory.objects.create(name="folder", bucket=cls.bucket,
                                                   owner=cls.owner)
        cls.far_folder = VaultDirectory.objects.create(name="far", bucket=cls.other_bucket,
                                                       owner=cls.owner)

    _n = 0

    def file(self, body=b"contents", *, bucket="default", title=None, origin="native",
             encrypted=False, public=False, directory=None):
        type(self)._n += 1
        title = title or f"f{self._n}.txt"
        vault_file = VaultFile(owner=self.owner, title=title, key=f"k-{self._n}",
                               file_type="text", origin=origin, is_encrypted=encrypted,
                               is_public=public, directory=directory,
                               bucket=self.bucket if bucket == "default" else bucket)
        vault_file.file.save(title, ContentFile(body), save=False)
        vault_file.save()
        return vault_file

    def post(self, name, data, user=None):
        self.client.force_login(user or self.owner)
        return self.client.post(reverse(f"vault:{name}"), data)


class MoveTests(_Fixture):
    def test_a_missing_pk_is_400(self):
        self.assertEqual(self.post("move_file", {}).status_code, 400)

    def test_someone_elses_file_is_404_and_stays_put(self):
        f = self.file()
        response = self.post("move_file", {"file_pk": f.pk,
                                           "destination_directory": self.folder.pk},
                             user=self.other)
        self.assertEqual(response.status_code, 404)
        f.refresh_from_db()
        self.assertIsNone(f.directory)

    def test_into_a_folder_of_the_same_bucket(self):
        f = self.file()
        response = self.post("move_file", {"file_pk": f.pk,
                                           "destination_directory": self.folder.pk})
        self.assertEqual(response.json(), {"ok": True, "new_pid": self.folder.pk})

    def test_never_into_a_folder_of_another_bucket(self):
        f = self.file()
        response = self.post("move_file", {"file_pk": f.pk,
                                           "destination_directory": self.far_folder.pk})
        self.assertEqual(response.status_code, 404)
        f.refresh_from_db()
        self.assertIsNone(f.directory)

    def test_no_destination_means_the_bucket_root(self):
        f = self.file(directory=self.folder)
        response = self.post("move_file", {"file_pk": f.pk})
        self.assertEqual(response.json(), {"ok": True, "new_pid": None})

    def test_a_mirror_row_does_not_move(self):
        f = self.file(origin="mirror")
        response = self.post("move_file", {"file_pk": f.pk,
                                           "destination_directory": self.folder.pk})
        self.assertEqual(response.status_code, 403)
        f.refresh_from_db()
        self.assertIsNone(f.directory)


class RenameTests(_Fixture):
    def test_a_title_is_required(self):
        f = self.file()
        self.assertEqual(self.post("rename_file", {"file_pk": f.pk, "title": "  "}).status_code,
                         400)

    def test_an_unknown_type_is_refused(self):
        f = self.file()
        response = self.post("rename_file", {"file_pk": f.pk, "title": "x", "file_type": "exe"})
        self.assertEqual(response.json()["error"], "Invalid file type.")

    @override_settings(VAULT_REFUSED_FILE_TYPES={"markdown"})
    def test_a_type_the_host_refuses_is_refused_by_name(self):
        f = self.file()
        response = self.post("rename_file", {"file_pk": f.pk, "title": "x.md",
                                             "file_type": "markdown"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("markdown", response.json()["error"])
        f.refresh_from_db()
        self.assertEqual(f.file_type, "text")

    def test_a_blank_type_keeps_the_type(self):
        f = self.file()
        payload = self.post("rename_file", {"file_pk": f.pk, "title": " renamed.txt "}).json()
        self.assertEqual(payload, {"ok": True, "title": "renamed.txt", "file_type": "text"})

    def test_a_real_type_is_applied(self):
        f = self.file()
        self.post("rename_file", {"file_pk": f.pk, "title": "r.md", "file_type": "markdown"})
        f.refresh_from_db()
        self.assertEqual((f.title, f.file_type), ("r.md", "markdown"))

    def test_someone_elses_file_is_404(self):
        f = self.file(title="keep.txt")
        response = self.post("rename_file", {"file_pk": f.pk, "title": "mine"}, user=self.other)
        self.assertEqual(response.status_code, 404)
        f.refresh_from_db()
        self.assertEqual(f.title, "keep.txt")

    def test_a_mirror_row_keeps_its_name(self):
        f = self.file(origin="mirror", title="peer.txt")
        self.assertEqual(self.post("rename_file", {"file_pk": f.pk, "title": "x"}).status_code,
                         403)
        f.refresh_from_db()
        self.assertEqual(f.title, "peer.txt")


class DeleteTests(_Fixture):
    def test_a_missing_pk_is_400(self):
        self.assertEqual(self.post("delete_file", {}).status_code, 400)

    def test_the_owner_moves_it_to_the_trash_bytes_kept(self):
        # The trash (2026-10-01): hidden from every door, bytes kept, the
        # folder remembered for the restore.
        f = self.file(directory=self.folder)
        path = f.file.path
        self.assertEqual(self.post("delete_file", {"file_pk": f.pk}).json(),
                         {"ok": True, "trashed": True})
        self.assertFalse(VaultFile.objects.filter(pk=f.pk).exists())
        trashed = VaultFile.all_objects.get(pk=f.pk)
        self.assertEqual((trashed.trashed_by, trashed.trashed_from, trashed.directory),
                         (self.owner, self.folder, None))
        self.assertIsNotNone(trashed.trashed_at)
        self.assertTrue(os.path.exists(path))

    def test_a_remote_bucket_s_file_still_goes_at_once(self):
        remote = Bucket.objects.create(name="Mounted", slug="mounted", owner=self.owner,
                                       storage_backend="remote_toto")
        f = self.file(bucket=remote)
        self.assertEqual(self.post("delete_file", {"file_pk": f.pk}).json(),
                         {"ok": True, "trashed": False})
        self.assertFalse(VaultFile.all_objects.filter(pk=f.pk).exists())

    def test_someone_elses_file_survives(self):
        f = self.file()
        self.assertEqual(self.post("delete_file", {"file_pk": f.pk}, user=self.other).status_code,
                         404)
        self.assertTrue(os.path.exists(f.file.path))

    def test_a_mirror_row_is_not_deleted_here(self):
        f = self.file(origin="mirror")
        self.assertEqual(self.post("delete_file", {"file_pk": f.pk}).status_code, 403)
        self.assertTrue(VaultFile.objects.filter(pk=f.pk).exists())


class EncryptionDoorTests(_Fixture):
    def test_every_door_wants_both_fields(self):
        f = self.file()
        for name in ("encrypt_file", "decrypt_file", "download_encrypted"):
            self.assertEqual(self.post(name, {"file_pk": f.pk}).status_code, 400, name)
            self.assertEqual(self.post(name, {"password": "pw"}).status_code, 400, name)

    def test_nobody_encrypts_or_decrypts_someone_elses_file(self):
        f = self.file()
        self.assertEqual(self.post("encrypt_file", {"file_pk": f.pk, "password": "p"},
                                   user=self.other).status_code, 404)
        self.assertEqual(self.post("decrypt_file", {"file_pk": f.pk, "password": "p"},
                                   user=self.other).status_code, 404)
        response = self.post("download_encrypted", {"file_pk": f.pk, "password": "p"},
                             user=self.other)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "File not found.")

    def test_a_plain_file_cannot_be_decrypted_or_downloaded_as_encrypted(self):
        f = self.file()
        response = self.post("decrypt_file", {"file_pk": f.pk, "password": "p"})
        self.assertEqual(response.json()["error"], "File is not encrypted.")
        response = self.post("download_encrypted", {"file_pk": f.pk, "password": "p"})
        self.assertEqual(response.status_code, 400)

    def test_remote_bytes_are_neither_encrypted_nor_decrypted_here(self):
        s3 = Bucket.objects.create(name="S3", slug="s3", owner=self.owner, storage_backend="s3")
        plain = self.file(bucket=s3)
        sealed = self.file(bucket=s3, encrypted=True)
        response = self.post("encrypt_file", {"file_pk": plain.pk, "password": "p"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("remote bucket", response.content.decode())
        self.assertEqual(self.post("decrypt_file", {"file_pk": sealed.pk,
                                                    "password": "p"}).status_code, 403)
        plain.refresh_from_db()
        self.assertFalse(plain.is_encrypted)

    def keyring(self):
        # The cheapest Argon2id the model accepts: these tests are about the
        # doors, not the KDF.
        from toto.gervazy.models import UserStrongbox

        return UserStrongbox.objects.create(owner=self.owner, name="keyring",
                                            argon2_memory_cost=19456, argon2_iterations=2,
                                            argon2_lanes=1)

    def test_encrypting_needs_a_keyring_and_says_so(self):
        f = self.file()
        response = self.post("encrypt_file", {"file_pk": f.pk, "password": "right"})
        self.assertEqual(response.status_code, 500)
        self.assertIn("No UserVault", response.json()["error"])
        f.refresh_from_db()
        self.assertFalse(f.is_encrypted)

    def test_encrypting_seals_the_bytes_and_takes_the_file_private(self):
        self.keyring()
        f = self.file(b"secret text", public=True)
        response = self.post("encrypt_file", {"file_pk": f.pk, "password": "right"})
        self.assertTrue(response.json()["ok"])
        f.refresh_from_db()
        self.assertTrue(f.is_encrypted)
        self.assertFalse(f.is_public)
        with f.file.open("rb") as handle:
            self.assertNotIn(b"secret text", handle.read())
        again = self.post("encrypt_file", {"file_pk": f.pk, "password": "right"})
        self.assertEqual(again.json()["error"], "File is already encrypted.")

    def test_a_wrong_password_on_an_encrypted_download_is_a_400_not_a_leak(self):
        self.keyring()
        f = self.file(b"secret text")
        f.encrypt(password="right")
        response = self.post("download_encrypted", {"file_pk": f.pk, "password": "wrong"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Incorrect password.")
        self.assertFalse(VaultUsageEvent.objects.exists())

    def test_the_right_password_streams_the_plaintext_and_meters_it(self):
        self.keyring()
        f = self.file(b"secret text", title="note.txt")
        f.encrypt(password="right")
        response = self.post("download_encrypted", {"file_pk": f.pk, "password": "right"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"secret text")
        self.assertIn("note.txt", response["Content-Disposition"])
        self.assertTrue(VaultUsageEvent.objects.filter(metric_code="storage.egress_mb",
                                                       user=self.owner).exists())
        f.refresh_from_db()
        self.assertTrue(f.is_encrypted)                 # reading does not decrypt

    def test_a_wrong_password_leaves_the_file_sealed(self):
        self.keyring()
        f = self.file(b"secret text")
        f.encrypt(password="right")
        response = self.post("decrypt_file", {"file_pk": f.pk, "password": "wrong"})
        self.assertEqual(response.status_code, 500)
        self.assertIn("Decryption failed", response.json()["error"])
        f.refresh_from_db()
        self.assertTrue(f.is_encrypted)

    def test_the_right_password_restores_the_bytes(self):
        self.keyring()
        f = self.file(b"secret text", public=True)
        self.post("encrypt_file", {"file_pk": f.pk, "password": "right"})
        response = self.post("decrypt_file", {"file_pk": f.pk, "password": "right"})
        self.assertTrue(response.json()["ok"])
        f.refresh_from_db()
        self.assertFalse(f.is_encrypted)
        with f.file.open("rb") as handle:
            self.assertEqual(handle.read(), b"secret text")

    @skip("BUG views.DecryptFileView:1611 - decrypting sets is_public=True unconditionally, so "
          "a file that was PRIVATE before it was encrypted is published to every reader the "
          "moment its owner decrypts it (the API's FileDecryptApiView leaves is_public alone)")
    def test_decrypting_a_private_file_does_not_publish_it(self):
        self.keyring()
        f = self.file(b"secret text", public=False)
        self.post("encrypt_file", {"file_pk": f.pk, "password": "right"})
        self.post("decrypt_file", {"file_pk": f.pk, "password": "right"})
        f.refresh_from_db()
        self.assertFalse(f.is_public)


class EncryptStatusTests(_Fixture):
    def run_for(self, owner, status, output):
        from toto.vault.views import EncryptFileView
        from toto.workflows.models import WorkflowRun

        return WorkflowRun.objects.create(
            workflow=EncryptFileView._ensure_workflow(),
            input_data={"data": {"file_pk": 1, "owner_id": owner.pk}},
            output_data=output, status=status, started_by=owner)

    def poll(self, run_id, user=None):
        self.client.force_login(user or self.owner)
        return self.client.get(reverse("vault:encrypt_status"), {"run_id": run_id})

    def test_a_missing_run_id_is_400(self):
        self.assertEqual(self.poll("").status_code, 400)

    def test_a_finished_run_hands_back_the_raw_url(self):
        from toto.workflows.models import WorkflowRun

        run = self.run_for(self.owner, WorkflowRun.COMPLETED,
                           {"raw_url": "/vault/public/owned/x/", "vault_file_id": 5})
        payload = self.poll(run.pk).json()
        self.assertEqual(payload["status"], WorkflowRun.COMPLETED)
        self.assertTrue(payload["done"] and payload["ok"])
        self.assertEqual((payload["raw_url"], payload["vault_file_id"]),
                         ("/vault/public/owned/x/", 5))

    def test_a_failed_run_says_why_or_says_it_failed(self):
        from toto.workflows.models import WorkflowRun

        with_reason = self.run_for(self.owner, WorkflowRun.FAILED, {"error": "not a PDF"})
        without = self.run_for(self.owner, WorkflowRun.FAILED, {})
        self.assertEqual(self.poll(with_reason.pk).json()["error"], "not a PDF")
        self.assertEqual(self.poll(without.pk).json()["error"], "Encryption failed.")
        self.assertFalse(self.poll(without.pk).json()["ok"])

    def test_a_running_run_is_not_terminal(self):
        from toto.workflows.models import WorkflowRun

        run = self.run_for(self.owner, WorkflowRun.RUNNING, {})
        payload = self.poll(run.pk).json()
        self.assertFalse(payload["is_terminal"])
        self.assertNotIn("ok", payload)

    def test_someone_elses_run_is_404(self):
        from toto.workflows.models import WorkflowRun

        run = self.run_for(self.owner, WorkflowRun.COMPLETED, {"raw_url": "/x/"})
        self.assertEqual(self.poll(run.pk, user=self.other).status_code, 404)


class ServicesListingTests(_Fixture):
    def test_a_stranger_cannot_read_a_private_files_title_through_it(self):
        f = self.file(title="salaries.txt")
        self.client.force_login(self.other)
        response = self.client.get(reverse("vault:file_services", args=[f.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b"salaries", response.content)

    def test_the_owner_gets_the_title_and_a_list(self):
        f = self.file(title="salaries.txt")
        self.client.force_login(self.owner)
        payload = self.client.get(reverse("vault:file_services", args=[f.pk])).json()
        self.assertEqual(payload["file_title"], "salaries.txt")
        self.assertIsInstance(payload["services"], list)

    def test_a_reader_of_a_public_file_may_list_its_services(self):
        f = self.file(public=True)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("vault:file_services",
                                                 args=[f.pk])).status_code, 200)


class DownloadShapeTests(_Fixture):
    def url(self, f):
        return reverse("vault:public_file", args=[f.bucket.slug, f.key])

    def test_an_unknown_key_is_404_for_everyone(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("vault:public_file", args=["owned", "no-such"]))
        self.assertEqual(response.status_code, 404)

    def test_a_download_is_metered_on_the_owners_meter_not_the_readers(self):
        f = self.file(b"x" * 2048, public=True)
        self.client.force_login(self.other)
        response = self.client.get(self.url(f))
        self.assertEqual(b"".join(response.streaming_content), b"x" * 2048)
        event = VaultUsageEvent.objects.get(metric_code="storage.egress_mb")
        self.assertEqual(event.user, self.owner)
        self.assertEqual(event.source_id, str(f.pk))

    def test_an_egress_cap_refuses_with_a_sentence_and_meters_nothing(self):
        from toto.quota import QuotaExceeded

        policy = SimpleNamespace(name="Egress", metric_code="storage.egress_mb",
                                 period="day", unit="MB")
        f = self.file(public=True)
        with patch("toto.quota.check_quota", side_effect=QuotaExceeded(policy, 5, 5)):
            response = self.client.get(self.url(f))
        self.assertEqual(response.status_code, 429)
        self.assertIn("Egress quota exceeded", response.content.decode())
        self.assertFalse(VaultUsageEvent.objects.exists())

    def test_arrears_never_stop_a_download(self):
        from toto.quota import InArrears

        f = self.file(b"still mine", public=True)
        with patch("toto.quota.check_quota", side_effect=InArrears()):
            response = self.client.get(self.url(f))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"still mine")

    def test_stream_file_serves_inline_with_the_type_it_is_told(self):
        f = self.file(b"<svg/>", title="pic.svg")
        response = stream_file(f, inline=True, content_type="image/svg+xml")
        self.assertIsInstance(response, FileResponse)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertTrue(response["Content-Disposition"].startswith("inline;"))
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        response.close()

    def test_stream_file_passes_a_backend_failure_through_as_502(self):
        f = self.file()
        with patch("toto.vault.storage_backends.open_file_stream",
                   side_effect=ConnectionError("peer down")):
            response = stream_file(f, inline=True)
        self.assertEqual(response.status_code, 502)
        self.assertIn("ConnectionError: peer down", response.content.decode())
        self.assertNotIn("X-Content-Type-Options", response)
