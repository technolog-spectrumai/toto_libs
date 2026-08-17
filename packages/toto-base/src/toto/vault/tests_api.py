import tempfile
import json

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from toto.vault.models import VaultFile, Bucket, VaultDirectory

User = get_user_model()

SMALL_TXT = b"hello vault"



# MEDIA_ROOT, not the host's: the real media dir is a root-owned docker bind
# mount, so every file-creating test here died on PermissionError before it
# asserted anything. The classes that already scoped their own temp dir
# (CopyFilesToBucketTest and friends in tests.py) are why the idiom exists;
# these ones never got it.
_MEDIA = tempfile.mkdtemp(prefix="vault-test-")

@override_settings(MEDIA_ROOT=_MEDIA)
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


@override_settings(MEDIA_ROOT=_MEDIA)
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


@override_settings(MEDIA_ROOT=_MEDIA)
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
        # Owner-scoped lookup: another user's file is indistinguishable from a
        # missing one (404, not a 403 that would confirm the key exists) — and it
        # avoids an unscoped get() that could 500 on a shared key.
        self.client.force_login(self.user)
        res = self.client.delete(f"/vault/api/files/{self.other_file.key}/")
        self.assertEqual(res.status_code, 404)
        self.assertTrue(VaultFile.objects.filter(pk=self.other_file.pk).exists())

    def test_delete_own_file(self):
        self.client.force_login(self.user)
        res = self.client.delete(f"/vault/api/files/{self.own_file.key}/")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(VaultFile.objects.filter(pk=self.own_file.pk).exists())

    def test_delete_missing_file(self):
        self.client.force_login(self.user)
        res = self.client.delete("/vault/api/files/nonexistent-key/")
        self.assertEqual(res.status_code, 404)


@override_settings(MEDIA_ROOT=_MEDIA)
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


@override_settings(MEDIA_ROOT=_MEDIA)
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


@override_settings(MEDIA_ROOT=_MEDIA)
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

    def test_get_content_carries_the_hash_a_save_must_send_back(self):
        # Without this the client has nothing to prove its edit was based on
        # the current state, and every desktop save is a blind overwrite.
        key = self._upload("notes.txt", b"line one", "text/plain")
        body = self.client.get(f"/vault/api/files/{key}/content/").json()
        self.assertTrue(body["content_hash"])
        self.assertIsNone(body["locked_by"])

    def test_a_save_built_on_a_stale_read_is_refused_and_kept(self):
        # The clobber this endpoint used to allow, and the reason the desktop
        # client opened vault files read-only.
        from toto.vault.models import FileVersion

        key = self._upload("shared.txt", b"original", "text/plain")
        stale = self.client.get(f"/vault/api/files/{key}/content/").json()["content_hash"]

        # Somebody else saves first.
        self.client.put(f"/vault/api/files/{key}/content/",
                        data=json.dumps({"content": "their work"}),
                        content_type="application/json")

        # Our save, built on what we read before that.
        res = self.client.put(
            f"/vault/api/files/{key}/content/",
            data=json.dumps({"content": "my work", "base_hash": stale}),
            content_type="application/json")
        self.assertEqual(res.status_code, 409)

        # Refused — and their work is still what is stored.
        stored = self.client.get(f"/vault/api/files/{key}/content/").json()
        self.assertEqual(stored["content"], "their work")

        # But ours was not thrown away.
        self.assertIsNotNone(res.json()["kept_as_version"])
        vf = VaultFile.objects.get(key=key, owner=self.user)
        rescued = FileVersion.objects.filter(file=vf, is_conflict=True)
        self.assertEqual(rescued.count(), 1)

    def test_a_save_on_the_current_state_goes_through(self):
        key = self._upload("mine.txt", b"original", "text/plain")
        current = self.client.get(f"/vault/api/files/{key}/content/").json()["content_hash"]
        res = self.client.put(
            f"/vault/api/files/{key}/content/",
            data=json.dumps({"content": "revised", "base_hash": current}),
            content_type="application/json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            self.client.get(f"/vault/api/files/{key}/content/").json()["content"],
            "revised")

    def test_a_save_snapshots_what_it_replaces(self):
        # A desktop client holds no lock, so the state it overwrites is the
        # only copy of whatever it did not see.
        from toto.vault.models import FileVersion

        key = self._upload("hist.txt", b"first", "text/plain")
        self.client.put(f"/vault/api/files/{key}/content/",
                        data=json.dumps({"content": "second"}),
                        content_type="application/json")
        vf = VaultFile.objects.get(key=key, owner=self.user)
        self.assertTrue(FileVersion.objects.filter(file=vf).exists())

    def test_a_file_someone_else_is_editing_refuses_the_save(self):
        # 423, not 409: a retry cannot succeed until they leave.
        from toto.vault import locks

        other = User.objects.create_user(username="holder", password="pass")
        key = self._upload("locked.txt", b"body", "text/plain")
        vf = VaultFile.objects.get(key=key, owner=self.user)
        locks.acquire(vf, other)

        res = self.client.put(f"/vault/api/files/{key}/content/",
                              data=json.dumps({"content": "mine"}),
                              content_type="application/json")
        self.assertEqual(res.status_code, 423)
        self.assertEqual(res.json()["locked_by"], "holder")
        self.assertEqual(
            self.client.get(f"/vault/api/files/{key}/content/").json()["content"], "body")

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


@override_settings(MEDIA_ROOT=_MEDIA)
class DirectoryCreateApiTests(TestCase):
    """POST /vault/api/directories/ — the Enigma Cloud mkdir endpoint."""

    def setUp(self):
        self.user = User.objects.create_user(username="mkdiruser", password="pass")
        self.other = User.objects.create_user(username="mkdirother", password="pass")
        self.bucket = Bucket.objects.create(
            owner=self.user, name="Mk", slug="mk", storage_backend="local"
        )

    def _mkdir(self, payload):
        return self.client.post(
            "/vault/api/directories/", json.dumps(payload),
            content_type="application/json",
        )

    def test_mkdir_unauthenticated(self):
        res = self._mkdir({"bucket_slug": "mk", "name": "docs"})
        self.assertEqual(res.status_code, 401)

    def test_mkdir_success(self):
        self.client.force_login(self.user)
        res = self._mkdir({"bucket_slug": "mk", "name": "docs"})
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["name"], "docs")
        self.assertIsNone(data["parent_id"])
        self.assertEqual(data["path"], "docs")
        self.assertEqual(data["bucket_slug"], "mk")
        d = VaultDirectory.objects.get(pk=data["id"])
        self.assertEqual(d.owner, self.user)
        self.assertEqual(d.bucket, self.bucket)

    def test_mkdir_nested_under_parent(self):
        self.client.force_login(self.user)
        parent = VaultDirectory.objects.create(
            name="docs", bucket=self.bucket, owner=self.user
        )
        res = self._mkdir({"bucket_slug": "mk", "name": "sub", "parent_id": parent.id})
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["parent_id"], parent.id)
        self.assertEqual(data["path"], "docs/sub")

    def test_mkdir_defaults_to_personal_bucket(self):
        self.client.force_login(self.user)
        res = self._mkdir({"name": "inbox"})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["bucket_slug"], f"personal-{self.user.username}")

    def test_mkdir_duplicate_name(self):
        self.client.force_login(self.user)
        VaultDirectory.objects.create(name="docs", bucket=self.bucket, owner=self.user)
        res = self._mkdir({"bucket_slug": "mk", "name": "docs"})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"], "Directory already exists.")

    def test_mkdir_same_name_under_different_parent_ok(self):
        self.client.force_login(self.user)
        parent = VaultDirectory.objects.create(
            name="docs", bucket=self.bucket, owner=self.user
        )
        res = self._mkdir({"bucket_slug": "mk", "name": "docs", "parent_id": parent.id})
        self.assertEqual(res.status_code, 201)

    def test_mkdir_bad_parent(self):
        self.client.force_login(self.user)
        other_bucket = Bucket.objects.create(
            owner=self.user, name="Mk2", slug="mk2", storage_backend="local"
        )
        foreign_parent = VaultDirectory.objects.create(
            name="elsewhere", bucket=other_bucket, owner=self.user
        )
        res = self._mkdir(
            {"bucket_slug": "mk", "name": "sub", "parent_id": foreign_parent.id}
        )
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["error"], "Parent directory not found.")

    def test_mkdir_nonexistent_parent(self):
        self.client.force_login(self.user)
        res = self._mkdir({"bucket_slug": "mk", "name": "sub", "parent_id": 999999})
        self.assertEqual(res.status_code, 404)

    def test_mkdir_foreign_bucket(self):
        Bucket.objects.create(
            owner=self.other, name="Foreign", slug="foreign", storage_backend="local"
        )
        self.client.force_login(self.user)
        res = self._mkdir({"bucket_slug": "foreign", "name": "docs"})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["error"], "Bucket not found.")

    def test_mkdir_missing_name(self):
        self.client.force_login(self.user)
        for payload in ({}, {"name": ""}, {"name": "   "}, {"bucket_slug": "mk"}):
            res = self._mkdir(payload)
            self.assertEqual(res.status_code, 400)

    def test_mkdir_invalid_json(self):
        self.client.force_login(self.user)
        res = self.client.post(
            "/vault/api/directories/", "nope", content_type="application/json"
        )
        self.assertEqual(res.status_code, 400)


@override_settings(MEDIA_ROOT=_MEDIA)
class DirectoryDeleteApiTests(TestCase):
    """DELETE /vault/api/directories/<pk>/ — the Enigma Cloud rmdir endpoint."""

    def setUp(self):
        self.user = User.objects.create_user(username="rmdiruser", password="pass")
        self.other = User.objects.create_user(username="rmdirother", password="pass")
        self.bucket = Bucket.objects.create(
            owner=self.user, name="Rm", slug="rm", storage_backend="local"
        )
        self.directory = VaultDirectory.objects.create(
            name="doomed", bucket=self.bucket, owner=self.user
        )

    def test_rmdir_unauthenticated(self):
        res = self.client.delete(f"/vault/api/directories/{self.directory.pk}/")
        self.assertEqual(res.status_code, 401)

    def test_rmdir_empty(self):
        self.client.force_login(self.user)
        res = self.client.delete(f"/vault/api/directories/{self.directory.pk}/")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(VaultDirectory.objects.filter(pk=self.directory.pk).exists())

    def test_rmdir_with_file_inside(self):
        VaultFile.objects.create(
            owner=self.user, title="Keep", key="keep", file="vault/files/keep.txt",
            file_type="text", bucket=self.bucket, directory=self.directory,
        )
        self.client.force_login(self.user)
        res = self.client.delete(f"/vault/api/directories/{self.directory.pk}/")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"], "Directory is not empty.")
        self.assertTrue(VaultDirectory.objects.filter(pk=self.directory.pk).exists())

    def test_rmdir_with_subdirectory(self):
        VaultDirectory.objects.create(
            name="child", bucket=self.bucket, owner=self.user, parent=self.directory
        )
        self.client.force_login(self.user)
        res = self.client.delete(f"/vault/api/directories/{self.directory.pk}/")
        self.assertEqual(res.status_code, 409)

    def test_rmdir_foreign_directory(self):
        self.client.force_login(self.other)
        res = self.client.delete(f"/vault/api/directories/{self.directory.pk}/")
        self.assertEqual(res.status_code, 404)
        self.assertTrue(VaultDirectory.objects.filter(pk=self.directory.pk).exists())

    def test_rmdir_nonexistent(self):
        self.client.force_login(self.user)
        res = self.client.delete("/vault/api/directories/999999/")
        self.assertEqual(res.status_code, 404)

    def test_rmdir_huge_pk_is_404_not_500(self):
        # <int:pk> matches unbounded digits; an out-of-range pk must 404, not 500.
        self.client.force_login(self.user)
        res = self.client.delete("/vault/api/directories/" + "9" * 30 + "/")
        self.assertEqual(res.status_code, 404)


@override_settings(MEDIA_ROOT=_MEDIA)
class FileUploadDirectoryApiTests(TestCase):
    """Upload with the optional directory_id form field."""

    def setUp(self):
        self.user = User.objects.create_user(username="dirup", password="pass")
        self.bucket = Bucket.objects.create(
            owner=self.user, name="Up", slug="up", storage_backend="local"
        )
        self.directory = VaultDirectory.objects.create(
            name="inbox", bucket=self.bucket, owner=self.user
        )
        self.client.force_login(self.user)

    def test_upload_into_directory(self):
        f = SimpleUploadedFile("note.txt", SMALL_TXT, content_type="text/plain")
        res = self.client.post(
            "/vault/api/files/upload/",
            {"file": f, "title": "In dir", "bucket_slug": "up",
             "directory_id": self.directory.id},
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["directory_id"], self.directory.id)
        vf = VaultFile.objects.get(owner=self.user, key=data["key"])
        self.assertEqual(vf.directory, self.directory)

    def test_upload_bad_directory_id(self):
        # The directory exists, but not in the resolved (default personal) bucket.
        f = SimpleUploadedFile("note.txt", SMALL_TXT, content_type="text/plain")
        res = self.client.post(
            "/vault/api/files/upload/",
            {"file": f, "title": "Lost", "directory_id": self.directory.id},
        )
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["error"], "Directory not found.")
        self.assertFalse(VaultFile.objects.filter(owner=self.user, title="Lost").exists())

    def test_upload_nonexistent_directory_id(self):
        f = SimpleUploadedFile("note.txt", SMALL_TXT, content_type="text/plain")
        res = self.client.post(
            "/vault/api/files/upload/",
            {"file": f, "bucket_slug": "up", "directory_id": 999999},
        )
        self.assertEqual(res.status_code, 404)

    def test_upload_without_directory_id_lands_in_root(self):
        f = SimpleUploadedFile("note.txt", SMALL_TXT, content_type="text/plain")
        res = self.client.post(
            "/vault/api/files/upload/", {"file": f, "bucket_slug": "up"}
        )
        self.assertEqual(res.status_code, 201)
        self.assertIsNone(res.json()["directory_id"])

    def test_upload_malformed_directory_id_is_404_not_500(self):
        # Non-numeric and out-of-range (would overflow the DB int) must 404 cleanly.
        for bad in ("abc", "9" * 30):
            f = SimpleUploadedFile("n.txt", SMALL_TXT, content_type="text/plain")
            res = self.client.post(
                "/vault/api/files/upload/",
                {"file": f, "bucket_slug": "up", "directory_id": bad},
            )
            self.assertEqual(res.status_code, 404, bad)

    def test_upload_into_foreign_bucket_slug_is_404_not_500(self):
        # A bucket slug owned by someone else must 404, not IntegrityError (slug is
        # globally unique).
        other = User.objects.create_user(username="stranger", password="pass")
        Bucket.objects.create(
            owner=other, name="Theirs", slug="theirs-bkt", storage_backend="local"
        )
        f = SimpleUploadedFile("n.txt", SMALL_TXT, content_type="text/plain")
        res = self.client.post(
            "/vault/api/files/upload/", {"file": f, "bucket_slug": "theirs-bkt"}
        )
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["error"], "Bucket not found.")

    def test_same_title_across_buckets_gets_unique_keys(self):
        # Keys must be unique per owner so the key-addressed detail/delete/download
        # endpoints never hit two rows (MultipleObjectsReturned → 500).
        Bucket.objects.create(
            owner=self.user, name="B2", slug="up2", storage_backend="local"
        )
        keys = []
        # Real (minimal) PDF bytes: the antivirus screens pdf at this door now,
        # and a file CLAIMING .pdf whose bytes are plain text is rightly
        # refused as the wrong shape. This test is about key uniqueness, so its
        # fixture has to be an honest PDF.
        small_pdf = b"%PDF-1.4\ntrailer<</Size 1>>\n%%EOF"
        for slug in ("up", "up2"):
            f = SimpleUploadedFile("report.pdf", small_pdf, content_type="application/pdf")
            res = self.client.post(
                "/vault/api/files/upload/",
                {"file": f, "title": "Report", "bucket_slug": slug},
            )
            self.assertEqual(res.status_code, 201)
            keys.append(res.json()["key"])
        self.assertNotEqual(keys[0], keys[1])
        # Both are addressable without a 500.
        for k in keys:
            self.assertEqual(self.client.get(f"/vault/api/files/{k}/").status_code, 200)


@override_settings(MEDIA_ROOT=_MEDIA)
class FileMoveApiTests(TestCase):
    """PATCH /vault/api/files/<key>/ with directory_id — move between folders."""

    def setUp(self):
        self.user = User.objects.create_user(username="mover", password="pass")
        self.bucket = Bucket.objects.create(
            owner=self.user, name="Mv", slug="mv", storage_backend="local"
        )
        self.directory = VaultDirectory.objects.create(
            name="dest", bucket=self.bucket, owner=self.user
        )
        self.vf = VaultFile.objects.create(
            owner=self.user, title="Wanderer", key="wanderer",
            file="vault/files/wanderer.txt", file_type="text", bucket=self.bucket,
        )
        self.client.force_login(self.user)

    def _patch(self, payload):
        return self.client.patch(
            f"/vault/api/files/{self.vf.key}/", json.dumps(payload),
            content_type="application/json",
        )

    def test_move_into_directory(self):
        res = self._patch({"directory_id": self.directory.id})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["directory_id"], self.directory.id)
        self.vf.refresh_from_db()
        self.assertEqual(self.vf.directory, self.directory)

    def test_move_to_root_via_null(self):
        self.vf.directory = self.directory
        self.vf.save(update_fields=["directory"])
        res = self._patch({"directory_id": None})
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.json()["directory_id"])
        self.vf.refresh_from_db()
        self.assertIsNone(self.vf.directory)

    def test_patch_without_directory_key_leaves_directory_alone(self):
        self.vf.directory = self.directory
        self.vf.save(update_fields=["directory"])
        res = self._patch({"title": "Renamed"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["title"], "Renamed")
        self.assertEqual(data["directory_id"], self.directory.id)

    def test_move_and_rename_combined(self):
        res = self._patch({"title": "Both", "directory_id": self.directory.id})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["title"], "Both")
        self.assertEqual(data["directory_id"], self.directory.id)

    def test_move_to_directory_in_other_bucket(self):
        other_bucket = Bucket.objects.create(
            owner=self.user, name="Mv2", slug="mv2", storage_backend="local"
        )
        foreign_dir = VaultDirectory.objects.create(
            name="afar", bucket=other_bucket, owner=self.user
        )
        res = self._patch({"directory_id": foreign_dir.id})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["error"], "Directory not found.")
        self.vf.refresh_from_db()
        self.assertIsNone(self.vf.directory)

    def test_move_to_nonexistent_directory(self):
        res = self._patch({"directory_id": 999999})
        self.assertEqual(res.status_code, 404)

    def test_move_malformed_directory_id_is_404_not_500(self):
        # Non-numeric and out-of-range values must 404, never 500.
        for bad in ("abc", 10 ** 30):
            res = self._patch({"directory_id": bad})
            self.assertEqual(res.status_code, 404, bad)
        self.vf.refresh_from_db()
        self.assertIsNone(self.vf.directory)


class StrongboxApiTests(TestCase):
    """The KDF record the desktop app derives its Fernet key from."""

    def setUp(self):
        self.owner = User.objects.create_user(username="sbowner", password="pass")
        self.other = User.objects.create_user(username="sbother", password="pass")

    def test_needs_auth(self):
        self.client.logout()
        self.assertEqual(self.client.get("/vault/api/strongbox/").status_code, 401)

    def test_returns_my_record_and_creates_it_on_first_ask(self):
        import base64

        self.client.force_login(self.owner)
        self.assertFalse(self.owner.user_strongboxes.exists())

        data = self.client.get("/vault/api/strongbox/").json()
        self.assertEqual(data["kdf"], "argon2id")
        self.assertEqual(len(base64.b64decode(data["salt"])), 16)
        self.assertGreaterEqual(data["memory_cost"], 19456)

        # Idempotent: asking again must return the SAME salt, or a key derived
        # on one device could never open a file sealed on another.
        again = self.client.get("/vault/api/strongbox/").json()
        self.assertEqual(again["salt"], data["salt"])
        self.assertEqual(self.owner.user_strongboxes.count(), 1)

    def test_never_reveals_anyone_elses(self):
        import base64

        self.client.force_login(self.owner)
        mine = self.client.get("/vault/api/strongbox/").json()
        self.client.force_login(self.other)
        theirs = self.client.get("/vault/api/strongbox/").json()
        self.assertNotEqual(mine["salt"], theirs["salt"])


@override_settings(MEDIA_ROOT=_MEDIA)
class FileCreateWithContentApiTests(TestCase):
    """Pushing a NEW file the client already holds the bytes for.

    The desktop client writes a document offline and then wants it in the
    vault. Before this it could only ask the server to mint an EMPTY file of a
    type the server knew a starter for, which is a different question and a
    much narrower one.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="pusher", password="pass")
        self.other = User.objects.create_user(username="bystander", password="pass")
        self.bucket = Bucket.objects.create(
            owner=self.user, name="Push", slug="push", storage_backend="local")
        self.client.force_login(self.user)

    def create(self, **payload):
        return self.client.post("/vault/api/files/create/",
                                data=json.dumps(payload),
                                content_type="application/json")

    def test_it_pushes_a_file_with_its_content(self):
        res = self.create(bucket_slug="push", title="notes.txt",
                          file_type="text", content="written offline")
        self.assertEqual(res.status_code, 201)
        key = res.json()["key"]
        body = self.client.get(f"/vault/api/files/{key}/content/").json()
        self.assertEqual(body["content"], "written offline")
        self.assertTrue(body["content_hash"], "a pushed file must be saveable next time")

    def test_an_empty_create_still_needs_a_starter(self):
        # The New-file case is unchanged: we must know what an empty one is.
        self.assertEqual(self.create(bucket_slug="push", title="a.txt",
                                     file_type="text").status_code, 201)
        self.assertEqual(self.create(bucket_slug="push", title="b.deck",
                                     file_type="pxml").status_code, 400)

    def test_a_type_with_no_starter_can_still_be_pushed_with_content(self):
        # This is the whole point: the client has the bytes, so "what does an
        # empty one look like?" is a question nobody needs answered.
        res = self.create(bucket_slug="push", title="talk.pxml", file_type="pxml",
                          content=DECK)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["file_type"], "pxml")

    def test_a_pushed_file_is_screened(self):
        # A file arriving with its bytes is an upload in all but name.
        res = self.create(bucket_slug="push", title="bad.svg", file_type="svg",
                          content='<svg xmlns="http://www.w3.org/2000/svg">'
                                  "<script>alert(1)</script></svg>")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(VaultFile.objects.filter(title="bad.svg").exists())

    def test_a_type_that_is_not_writable_here_is_refused(self):
        self.assertEqual(
            self.create(bucket_slug="push", title="clip.mp4", file_type="video",
                        content="not really").status_code, 415)

    def test_it_will_not_push_into_somebody_elses_bucket(self):
        Bucket.objects.create(owner=self.other, name="Theirs", slug="theirs",
                              storage_backend="local")
        self.assertEqual(
            self.create(bucket_slug="theirs", title="x.txt", file_type="text",
                        content="hi").status_code, 404)

    def test_two_pushes_of_one_name_do_not_collide(self):
        first = self.create(bucket_slug="push", title="notes.txt",
                            file_type="text", content="one")
        second = self.create(bucket_slug="push", title="notes.txt",
                             file_type="text", content="two")
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.json()["key"], second.json()["key"])


#: A real deck — `pxml` has no starter content, which is exactly why it is the
#: interesting case for a push.
DECK = (
    '<?xml version="1.0"?>\n'
    '<presentation version="2" title="T" theme="black" font="sans">'
    '<slide id="s-1" layout="title-content"><title>t</title></slide>'
    "</presentation>\n"
)
