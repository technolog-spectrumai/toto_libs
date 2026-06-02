from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from toto.vault.models import VaultFile, Bucket

User = get_user_model()

SMALL_TXT = b"hello vault"


class FileListApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="vaultuser", password="pass")
        self.other = User.objects.create_user(username="other", password="pass")

    def test_list_unauthenticated(self):
        res = self.client.get("/vault/api/files/")
        self.assertEqual(res.status_code, 401)

    def test_list_own_files_only(self):
        bucket = Bucket.objects.create(
            owner=self.user, name="My Bucket", slug="my-bucket", storage_backend="local"
        )
        other_bucket = Bucket.objects.create(
            owner=self.other, name="Other Bucket", slug="other-bucket", storage_backend="local"
        )
        VaultFile.objects.create(
            owner=self.user, title="Mine", key="mine", file="vault/files/mine.txt",
            file_type="text", bucket=bucket,
        )
        VaultFile.objects.create(
            owner=self.other, title="Theirs", key="theirs", file="vault/files/theirs.txt",
            file_type="text", bucket=other_bucket,
        )
        self.client.force_login(self.user)
        res = self.client.get("/vault/api/files/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data["files"]), 1)
        self.assertEqual(data["files"][0]["title"], "Mine")


class FileUploadApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="uploader", password="pass")

    def test_upload_unauthenticated(self):
        f = SimpleUploadedFile("note.txt", SMALL_TXT, content_type="text/plain")
        res = self.client.post("/vault/api/files/upload/", {"file": f, "title": "Note"})
        self.assertEqual(res.status_code, 401)

    def test_upload_no_file(self):
        self.client.force_login(self.user)
        res = self.client.post("/vault/api/files/upload/", {"title": "No file"})
        self.assertEqual(res.status_code, 400)

    def test_upload_creates_file(self):
        self.client.force_login(self.user)
        f = SimpleUploadedFile("note.txt", SMALL_TXT, content_type="text/plain")
        res = self.client.post("/vault/api/files/upload/", {"file": f, "title": "My Note"})
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["title"], "My Note")
        self.assertEqual(data["file_type"], "text")
        self.assertTrue(VaultFile.objects.filter(owner=self.user, title="My Note").exists())

    def test_upload_auto_detects_image_type(self):
        self.client.force_login(self.user)
        f = SimpleUploadedFile("pic.png", b"\x89PNG\r\n", content_type="image/png")
        res = self.client.post("/vault/api/files/upload/", {"file": f, "title": "Pic"})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["file_type"], "image")


class FileDeleteApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="deleter", password="pass")
        self.other = User.objects.create_user(username="victim", password="pass")
        self.bucket = Bucket.objects.create(
            owner=self.user, name="Del Bucket", slug="del-bucket", storage_backend="local"
        )
        self.other_bucket = Bucket.objects.create(
            owner=self.other, name="Other Del Bucket", slug="other-del-bucket", storage_backend="local"
        )
        self.own_file = VaultFile.objects.create(
            owner=self.user, title="Own", key="own-file", file="vault/files/own.txt",
            file_type="text", bucket=self.bucket,
        )
        self.other_file = VaultFile.objects.create(
            owner=self.other, title="Other", key="other-file", file="vault/files/other.txt",
            file_type="text", bucket=self.other_bucket,
        )

    def test_delete_unauthenticated(self):
        res = self.client.delete(f"/vault/api/files/{self.own_file.key}/")
        self.assertEqual(res.status_code, 401)

    def test_delete_other_users_file(self):
        self.client.force_login(self.user)
        res = self.client.delete(f"/vault/api/files/{self.other_file.key}/")
        self.assertEqual(res.status_code, 403)

    def test_delete_own_file(self):
        self.client.force_login(self.user)
        res = self.client.delete(f"/vault/api/files/{self.own_file.key}/")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(VaultFile.objects.filter(pk=self.own_file.pk).exists())

    def test_delete_missing_file(self):
        self.client.force_login(self.user)
        res = self.client.delete("/vault/api/files/nonexistent-key/")
        self.assertEqual(res.status_code, 404)
