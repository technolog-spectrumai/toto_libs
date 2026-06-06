import json

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from toto.vault.models import VaultFile, Bucket, VaultDirectory

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


class FileListDirectoryFieldsTests(TestCase):
    """The list endpoint must surface directory_id + is_editable for the tree."""

    def setUp(self):
        self.user = User.objects.create_user(username="treeuser", password="pass")
        self.bucket = Bucket.objects.create(
            owner=self.user, name="Tree", slug="tree", storage_backend="local"
        )
        self.directory = VaultDirectory.objects.create(
            name="docs", bucket=self.bucket, owner=self.user
        )

    def test_list_includes_directory_and_editable_flags(self):
        VaultFile.objects.create(
            owner=self.user, title="Readme", key="readme", file="vault/files/readme.txt",
            file_type="text", bucket=self.bucket, directory=self.directory,
        )
        VaultFile.objects.create(
            owner=self.user, title="Photo", key="photo", file="vault/files/photo.png",
            file_type="image", bucket=self.bucket,
        )
        self.client.force_login(self.user)
        files = {f["key"]: f for f in self.client.get("/vault/api/files/").json()["files"]}
        self.assertEqual(files["readme"]["directory_id"], self.directory.id)
        self.assertTrue(files["readme"]["is_editable"])
        self.assertIsNone(files["photo"]["directory_id"])
        self.assertFalse(files["photo"]["is_editable"])  # image is not text-editable


class BucketTreeApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="treeowner", password="pass")
        self.other = User.objects.create_user(username="treeother", password="pass")

    def test_tree_unauthenticated(self):
        self.assertEqual(self.client.get("/vault/api/buckets/").status_code, 401)

    def test_tree_returns_nested_directories(self):
        bucket = Bucket.objects.create(
            owner=self.user, name="Code", slug="code", storage_backend="local"
        )
        src = VaultDirectory.objects.create(name="src", bucket=bucket, owner=self.user)
        VaultDirectory.objects.create(name="lib", bucket=bucket, owner=self.user, parent=src)
        self.client.force_login(self.user)
        data = self.client.get("/vault/api/buckets/").json()
        buckets = {b["slug"]: b for b in data["buckets"]}
        self.assertIn("code", buckets)
        dirs = {d["name"]: d for d in buckets["code"]["directories"]}
        self.assertEqual(dirs["src"]["parent_id"], None)
        self.assertEqual(dirs["lib"]["parent_id"], src.id)
        self.assertEqual(dirs["lib"]["path"], "src/lib")

    def test_tree_excludes_other_users_directories(self):
        my_bucket = Bucket.objects.create(
            owner=self.user, name="Mine", slug="mine", storage_backend="local"
        )
        VaultDirectory.objects.create(name="ok", bucket=my_bucket, owner=self.user)
        other_bucket = Bucket.objects.create(
            owner=self.other, name="Theirs", slug="theirs", storage_backend="local"
        )
        VaultDirectory.objects.create(name="secret", bucket=other_bucket, owner=self.other)
        self.client.force_login(self.user)
        data = self.client.get("/vault/api/buckets/").json()
        slugs = {b["slug"] for b in data["buckets"]}
        self.assertIn("mine", slugs)
        self.assertNotIn("theirs", slugs)


class FileContentApiTests(TestCase):
    """Round-trip the Ace editor read/write endpoints."""

    def setUp(self):
        self.user = User.objects.create_user(username="editor", password="pass")
        self.client.force_login(self.user)

    def _upload(self, name, content, content_type):
        f = SimpleUploadedFile(name, content, content_type=content_type)
        res = self.client.post("/vault/api/files/upload/", {"file": f, "title": name})
        self.assertEqual(res.status_code, 201)
        return res.json()["key"]

    def test_get_content_returns_text(self):
        key = self._upload("notes.txt", b"line one\nline two", "text/plain")
        res = self.client.get(f"/vault/api/files/{key}/content/")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["content"], "line one\nline two")
        self.assertTrue(body["is_editable"])

    def test_get_content_rejects_binary_type(self):
        key = self._upload("pic.png", b"\x89PNG\r\n\x1a\n", "image/png")
        res = self.client.get(f"/vault/api/files/{key}/content/")
        self.assertEqual(res.status_code, 415)

    def test_put_content_saves_changes(self):
        key = self._upload("doc.md", b"old", "text/plain")
        res = self.client.put(
            f"/vault/api/files/{key}/content/",
            data=json.dumps({"content": "brand new body"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        # Re-read through the API to confirm persistence.
        again = self.client.get(f"/vault/api/files/{key}/content/").json()
        self.assertEqual(again["content"], "brand new body")
        vf = VaultFile.objects.get(owner=self.user, key=key)
        self.assertEqual(vf.file_size_bytes, len("brand new body".encode("utf-8")))

    def test_put_content_requires_string(self):
        key = self._upload("doc2.md", b"x", "text/plain")
        res = self.client.put(
            f"/vault/api/files/{key}/content/",
            data=json.dumps({"content": 123}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)

    def test_content_missing_file(self):
        res = self.client.get("/vault/api/files/does-not-exist/content/")
        self.assertEqual(res.status_code, 404)

    def test_content_unauthenticated(self):
        key = self._upload("doc3.md", b"x", "text/plain")
        self.client.logout()
        res = self.client.get(f"/vault/api/files/{key}/content/")
        self.assertEqual(res.status_code, 401)
