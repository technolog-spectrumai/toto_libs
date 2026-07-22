import json
import shutil
import tempfile
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.vault.plugins import VaultPlayPlugin

from toto.core.models import Platform
from toto.vault.models import Bucket, VaultDirectory, VaultFile



class BucketQuotaTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user("admin", password="pass", is_staff=True)

    def test_bucket_quota_field(self):
        bucket = Bucket.objects.create(
            name="TestBucket",
            slug="testbucket",
            owner=self.user,
            storage_quota_mb=500,
        )
        self.assertEqual(bucket.storage_quota_mb, 500)

    def test_bucket_quota_nullable(self):
        bucket = Bucket.objects.create(
            name="UnlimitedBucket",
            slug="unlimitedbucket",
            owner=self.user,
            storage_quota_mb=None,
        )
        self.assertIsNone(bucket.storage_quota_mb)


class NoVaultAssetsLinkTest(TestCase):
    """Asserts that vault models contain no FK references to the assets app."""

    VAULT_MODELS = [Bucket, VaultDirectory]

    def test_no_assets_fk_in_vault_models(self):
        from django.db import models as _m
        for Model in self.VAULT_MODELS:
            for field in Model._meta.get_fields():
                if isinstance(field, (_m.ForeignKey, _m.OneToOneField, _m.ManyToManyField)):
                    related = getattr(field, 'related_model', None)
                    if related is None:
                        continue
                    app = related._meta.app_label
                    self.assertNotEqual(
                        app, "assets",
                        msg=f"{Model.__name__}.{field.name} points to assets app model {related.__name__}",
                    )


class CopyFilesToBucketTest(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temp_media = tempfile.mkdtemp()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self._override = override_settings(MEDIA_ROOT=self.temp_media)
        self._override.enable()

        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.alice = User.objects.create_user("alice", password="pass")
        self.bob = User.objects.create_user("bob", password="pass")
        self.client = Client()

        self.src_bucket = Bucket.objects.create(name="Source", slug="source", owner=self.alice)
        self.dst_bucket = Bucket.objects.create(name="Dest", slug="dest", owner=self.alice)
        self.bob_bucket = Bucket.objects.create(name="Bob", slug="bob-bucket", owner=self.bob)

        self.src_file = VaultFile.objects.create(
            owner=self.alice,
            title="test file",
            key="test-file",
            file=SimpleUploadedFile("test_file.txt", b"hello world"),
            file_type="text",
            bucket=self.src_bucket,
        )

    def tearDown(self):
        self._override.disable()

    def _copy_url(self, slug):
        return reverse("vault:copy_files", kwargs={"source_slug": slug})

    def test_copy_file_creates_new_record_in_target(self):
        self.client.login(username="alice", password="pass")
        response = self.client.post(self._copy_url(self.src_bucket.slug), {
            "files": [self.src_file.pk],
            "destination_bucket": self.dst_bucket.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            VaultFile.objects.filter(bucket=self.dst_bucket, title="test file").exists()
        )

    def test_source_file_remains_in_source_bucket(self):
        self.client.login(username="alice", password="pass")
        self.client.post(self._copy_url(self.src_bucket.slug), {
            "files": [self.src_file.pk],
            "destination_bucket": self.dst_bucket.pk,
        })
        self.assertTrue(
            VaultFile.objects.filter(pk=self.src_file.pk, bucket=self.src_bucket).exists()
        )

    def test_new_file_exists_only_in_target(self):
        self.client.login(username="alice", password="pass")
        self.client.post(self._copy_url(self.src_bucket.slug), {
            "files": [self.src_file.pk],
            "destination_bucket": self.dst_bucket.pk,
        })
        self.assertEqual(VaultFile.objects.filter(bucket=self.dst_bucket).count(), 1)
        self.assertEqual(VaultFile.objects.filter(bucket=self.src_bucket).count(), 1)

    def test_duplicate_key_is_renamed(self):
        VaultFile.objects.create(
            owner=self.alice,
            title="existing",
            key="test-file",
            file=SimpleUploadedFile("existing.txt", b"existing"),
            file_type="text",
            bucket=self.dst_bucket,
        )
        self.client.login(username="alice", password="pass")
        self.client.post(self._copy_url(self.src_bucket.slug), {
            "files": [self.src_file.pk],
            "destination_bucket": self.dst_bucket.pk,
        })
        keys = list(VaultFile.objects.filter(bucket=self.dst_bucket).values_list("key", flat=True))
        self.assertEqual(len(keys), 2)
        self.assertIn("test-file", keys)
        self.assertIn("test-file-1", keys)

    def test_cannot_copy_to_other_users_bucket(self):
        self.client.login(username="alice", password="pass")
        self.client.post(self._copy_url(self.src_bucket.slug), {
            "files": [self.src_file.pk],
            "destination_bucket": self.bob_bucket.pk,
        })
        self.assertFalse(VaultFile.objects.filter(bucket=self.bob_bucket).exists())

    def test_cannot_copy_from_other_users_bucket(self):
        self.client.login(username="alice", password="pass")
        response = self.client.get(self._copy_url(self.bob_bucket.slug))
        self.assertEqual(response.status_code, 404)

    def test_requires_login(self):
        response = self.client.get(self._copy_url(self.src_bucket.slug))
        self.assertNotEqual(response.status_code, 200)

    def test_redirect_to_target_bucket_on_success(self):
        self.client.login(username="alice", password="pass")
        response = self.client.post(self._copy_url(self.src_bucket.slug), {
            "files": [self.src_file.pk],
            "destination_bucket": self.dst_bucket.pk,
        })
        self.assertRedirects(
            response,
            reverse("vault:bucket_metrics", kwargs={"bucket_slug": self.dst_bucket.slug}),
        )


class BucketCopyAjaxViewTest(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temp_media = tempfile.mkdtemp()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self._override = override_settings(MEDIA_ROOT=self.temp_media)
        self._override.enable()

        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.alice = User.objects.create_user("alice", password="pass")
        self.bob = User.objects.create_user("bob", password="pass")
        self.client = Client()

        self.src_bucket = Bucket.objects.create(name="Source", slug="source-ajax", owner=self.alice)
        self.dst_bucket = Bucket.objects.create(name="Dest", slug="dest-ajax", owner=self.alice)
        self.bob_bucket = Bucket.objects.create(name="Bob", slug="bob-ajax", owner=self.bob)

        self.src_file = VaultFile.objects.create(
            owner=self.alice,
            title="ajax file",
            key="ajax-file",
            file=SimpleUploadedFile("ajax_file.txt", b"ajax content"),
            file_type="text",
            bucket=self.src_bucket,
        )

    def tearDown(self):
        self._override.disable()

    def _ajax_url(self, slug):
        return reverse("vault:copy_files_ajax", kwargs={"source_slug": slug})

    def _post(self, file_ids, dest_bucket_id):
        return self.client.post(
            self._ajax_url(self.src_bucket.slug),
            {"files": file_ids, "destination_bucket": dest_bucket_id},
        )

    def test_requires_login(self):
        response = self._post([self.src_file.pk], self.dst_bucket.pk)
        self.assertNotEqual(response.status_code, 200)

    def test_successful_copy_returns_ok_json(self):
        self.client.login(username="alice", password="pass")
        response = self._post([self.src_file.pk], self.dst_bucket.pk)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["dest_slug"], self.dst_bucket.slug)
        self.assertIn("dest_url", data)

    def test_copy_creates_file_in_destination(self):
        self.client.login(username="alice", password="pass")
        self._post([self.src_file.pk], self.dst_bucket.pk)
        self.assertTrue(VaultFile.objects.filter(bucket=self.dst_bucket, title="ajax file").exists())

    def test_source_file_unchanged(self):
        self.client.login(username="alice", password="pass")
        self._post([self.src_file.pk], self.dst_bucket.pk)
        self.assertTrue(VaultFile.objects.filter(pk=self.src_file.pk, bucket=self.src_bucket).exists())

    def test_empty_files_returns_error(self):
        self.client.login(username="alice", password="pass")
        response = self._post([], self.dst_bucket.pk)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])

    def test_missing_destination_returns_error(self):
        self.client.login(username="alice", password="pass")
        response = self.client.post(
            self._ajax_url(self.src_bucket.slug),
            {"files": [self.src_file.pk]},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])

    def test_cannot_copy_to_other_users_bucket(self):
        self.client.login(username="alice", password="pass")
        response = self._post([self.src_file.pk], self.bob_bucket.pk)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertFalse(VaultFile.objects.filter(bucket=self.bob_bucket).exists())

    def test_cannot_copy_from_other_users_bucket(self):
        self.client.login(username="alice", password="pass")
        response = self.client.post(
            self._ajax_url(self.bob_bucket.slug),
            {"files": [self.src_file.pk], "destination_bucket": self.dst_bucket.pk},
        )
        self.assertEqual(response.status_code, 404)

    def test_duplicate_key_renamed(self):
        VaultFile.objects.create(
            owner=self.alice,
            title="existing",
            key="ajax-file",
            file=SimpleUploadedFile("existing.txt", b"x"),
            file_type="text",
            bucket=self.dst_bucket,
        )
        self.client.login(username="alice", password="pass")
        self._post([self.src_file.pk], self.dst_bucket.pk)
        keys = list(VaultFile.objects.filter(bucket=self.dst_bucket).values_list("key", flat=True))
        self.assertEqual(len(keys), 2)
        self.assertIn("ajax-file", keys)
        self.assertIn("ajax-file-1", keys)

    def test_dest_url_points_to_correct_bucket(self):
        self.client.login(username="alice", password="pass")
        response = self._post([self.src_file.pk], self.dst_bucket.pk)
        data = response.json()
        expected_url = reverse("vault:bucket_metrics", kwargs={"bucket_slug": self.dst_bucket.slug})
        self.assertEqual(data["dest_url"], expected_url)


class BucketMetricsViewContextTest(TestCase):

    def setUp(self):
        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.alice = User.objects.create_user("alice", password="pass")
        self.bob = User.objects.create_user("bob", password="pass")
        self.client = Client()

        self.bucket = Bucket.objects.create(name="Main", slug="main-bucket", owner=self.alice)
        self.other_bucket = Bucket.objects.create(name="Other", slug="other-bucket", owner=self.alice)
        self.bob_bucket = Bucket.objects.create(name="BobBucket", slug="bob-main", owner=self.bob)

    def _url(self, slug=None):
        return reverse("vault:bucket_metrics", kwargs={"bucket_slug": slug or self.bucket.slug})

    def test_requires_login(self):
        response = self.client.get(self._url())
        self.assertNotEqual(response.status_code, 200)

    def test_page_loads(self):
        self.client.login(username="alice", password="pass")
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)

    def test_dest_buckets_excludes_current(self):
        self.client.login(username="alice", password="pass")
        response = self.client.get(self._url())
        dest_buckets = response.context["dest_buckets"]
        dest_pks = [b.pk for b in dest_buckets]
        self.assertNotIn(self.bucket.pk, dest_pks)
        self.assertIn(self.other_bucket.pk, dest_pks)

    def test_dest_buckets_excludes_other_users(self):
        self.client.login(username="alice", password="pass")
        response = self.client.get(self._url())
        dest_buckets = response.context["dest_buckets"]
        dest_pks = [b.pk for b in dest_buckets]
        self.assertNotIn(self.bob_bucket.pk, dest_pks)

    def test_copy_files_data_contains_only_own_files(self):
        import tempfile, shutil
        from django.test import override_settings
        temp_media = tempfile.mkdtemp()
        try:
            with override_settings(MEDIA_ROOT=temp_media):
                VaultFile.objects.create(
                    owner=self.alice, title="Alice File", key="alice-file",
                    file=SimpleUploadedFile("a.txt", b"a"), file_type="text",
                    bucket=self.bucket,
                )
                VaultFile.objects.create(
                    owner=self.bob, title="Bob File", key="bob-file",
                    file=SimpleUploadedFile("b.txt", b"b"), file_type="text",
                    bucket=self.bucket,
                )
                self.client.login(username="alice", password="pass")
                response = self.client.get(self._url())
                files_data = response.context["copy_files_data"]
                titles = [f["title"] for f in files_data]
                self.assertIn("Alice File", titles)
                self.assertNotIn("Bob File", titles)
        finally:
            shutil.rmtree(temp_media, ignore_errors=True)

    def test_copy_files_data_structure(self):
        import tempfile, shutil
        from django.test import override_settings
        temp_media = tempfile.mkdtemp()
        try:
            with override_settings(MEDIA_ROOT=temp_media):
                VaultFile.objects.create(
                    owner=self.alice, title="Structured File", key="struct-file",
                    file=SimpleUploadedFile("s.txt", b"s"), file_type="text",
                    bucket=self.bucket,
                )
                self.client.login(username="alice", password="pass")
                response = self.client.get(self._url())
                files_data = response.context["copy_files_data"]
                self.assertGreater(len(files_data), 0)
                entry = files_data[0]
                for key in ("id", "title", "file_type", "key"):
                    self.assertIn(key, entry)
        finally:
            shutil.rmtree(temp_media, ignore_errors=True)


class StorageDriverTest(TestCase):
    """Unit tests for vault/storage_backends.py."""

    @classmethod
    def setUpClass(cls):
        cls.temp_media = tempfile.mkdtemp()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self._override = override_settings(MEDIA_ROOT=self.temp_media)
        self._override.enable()

    def tearDown(self):
        self._override.disable()

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    def test_get_bucket_storage_returns_local_by_default(self):
        from toto.vault.storage_backends import get_bucket_storage, LocalVaultStorageDriver

        class FakeBucket:
            storage_backend = ""
            storage_config = {}

        self.assertIsInstance(get_bucket_storage(FakeBucket()), LocalVaultStorageDriver)

    def test_get_bucket_storage_local_explicit(self):
        from toto.vault.storage_backends import get_bucket_storage, LocalVaultStorageDriver

        class FakeBucket:
            storage_backend = "local"
            storage_config = {}

        self.assertIsInstance(get_bucket_storage(FakeBucket()), LocalVaultStorageDriver)

    def test_get_bucket_storage_s3(self):
        from toto.vault.storage_backends import get_bucket_storage, S3CompatibleVaultStorageDriver

        class FakeBucket:
            storage_backend = "s3"
            storage_config = {"bucket_name": "my-bucket"}

        self.assertIsInstance(get_bucket_storage(FakeBucket()), S3CompatibleVaultStorageDriver)

    def test_get_bucket_storage_none_backend_falls_back_to_local(self):
        from toto.vault.storage_backends import get_bucket_storage, LocalVaultStorageDriver

        class FakeBucket:
            storage_backend = None
            storage_config = None

        self.assertIsInstance(get_bucket_storage(FakeBucket()), LocalVaultStorageDriver)

    # ------------------------------------------------------------------
    # LocalVaultStorageDriver round-trip
    # ------------------------------------------------------------------

    def test_local_driver_round_trip(self):
        from toto.vault.storage_backends import LocalVaultStorageDriver

        driver = LocalVaultStorageDriver()
        stored = driver.save("vault/files/hello.txt", b"local content")
        self.assertTrue(driver.exists(stored))
        self.assertEqual(driver.read(stored), b"local content")
        driver.delete(stored)
        self.assertFalse(driver.exists(stored))

    # ------------------------------------------------------------------
    # S3CompatibleVaultStorageDriver — boto3 client construction
    # (boto3 may not be installed; we inject a fake module into sys.modules)
    # ------------------------------------------------------------------

    @staticmethod
    def _fake_boto3_modules():
        """
        Return a sys.modules patch dict with mock boto3/botocore so tests run
        without boto3 installed.  _build_client imports both lazily.
        """
        import sys

        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_session.client.return_value = mock_client

        mock_boto3 = MagicMock()
        mock_boto3.Session.return_value = mock_session

        mock_botocore_config = MagicMock()
        mock_botocore_config.Config = MagicMock(return_value=MagicMock())

        return (
            mock_boto3,
            mock_session,
            {
                "boto3": mock_boto3,
                "botocore": MagicMock(),
                "botocore.config": mock_botocore_config,
            },
        )

    def test_s3_driver_build_client_passes_endpoint_and_region(self):
        import sys
        from toto.vault.storage_backends import S3CompatibleVaultStorageDriver

        mock_boto3, mock_session, modules = self._fake_boto3_modules()
        config = {
            "bucket_name": "my-bucket",
            "endpoint_url": "https://s3.gra.perf.cloud.ovh.net",
            "region_name": "gra",
            "addressing_style": "path",
        }
        driver = S3CompatibleVaultStorageDriver(config)

        with patch.dict(sys.modules, modules):
            driver._build_client()

        _, call_kwargs = mock_session.client.call_args
        self.assertEqual(call_kwargs["endpoint_url"], "https://s3.gra.perf.cloud.ovh.net")
        self.assertEqual(call_kwargs["region_name"], "gra")

    def test_s3_driver_uses_named_profile(self):
        import sys
        from toto.vault.storage_backends import S3CompatibleVaultStorageDriver

        mock_boto3, _, modules = self._fake_boto3_modules()
        config = {"bucket_name": "my-bucket", "aws_profile": "ovh-prod"}
        driver = S3CompatibleVaultStorageDriver(config)

        with patch.dict(sys.modules, modules):
            driver._build_client()

        mock_boto3.Session.assert_called_once_with(profile_name="ovh-prod")

    def test_s3_driver_no_profile_uses_default_session(self):
        import sys
        from toto.vault.storage_backends import S3CompatibleVaultStorageDriver

        mock_boto3, _, modules = self._fake_boto3_modules()
        config = {"bucket_name": "my-bucket", "endpoint_url": "https://s3.example.com"}
        driver = S3CompatibleVaultStorageDriver(config)

        with patch.dict(sys.modules, modules):
            driver._build_client()

        mock_boto3.Session.assert_called_once_with()  # no profile_name kwarg

    def test_s3_driver_save_calls_put_object(self):
        from toto.vault.storage_backends import S3CompatibleVaultStorageDriver

        config = {"bucket_name": "my-bucket", "prefix": "vault/"}
        driver = S3CompatibleVaultStorageDriver(config)
        driver._client = MagicMock()

        stored_key = driver.save("test_file.txt", b"the content")

        driver._client.put_object.assert_called_once()
        kwargs = driver._client.put_object.call_args.kwargs
        self.assertEqual(kwargs["Bucket"], "my-bucket")
        self.assertTrue(kwargs["Key"].startswith("vault/"))
        self.assertIn("test_file", kwargs["Key"])
        self.assertEqual(kwargs["Body"], b"the content")
        self.assertEqual(stored_key, kwargs["Key"])

    def test_s3_driver_read_calls_get_object(self):
        from toto.vault.storage_backends import S3CompatibleVaultStorageDriver

        config = {"bucket_name": "my-bucket"}
        driver = S3CompatibleVaultStorageDriver(config)
        mock_client = MagicMock()
        mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: b"s3 bytes")}
        driver._client = mock_client

        result = driver.read("vault/some_key_abc.txt")

        mock_client.get_object.assert_called_once_with(Bucket="my-bucket", Key="vault/some_key_abc.txt")
        self.assertEqual(result, b"s3 bytes")

    def test_s3_driver_unique_keys_for_same_filename(self):
        """Two saves with the same filename hint must produce different S3 keys."""
        from toto.vault.storage_backends import S3CompatibleVaultStorageDriver

        config = {"bucket_name": "my-bucket", "prefix": "vault/"}
        driver = S3CompatibleVaultStorageDriver(config)
        driver._client = MagicMock()

        key1 = driver.save("report.pdf", b"a")
        key2 = driver.save("report.pdf", b"b")

        self.assertNotEqual(key1, key2)
        self.assertTrue(key1.startswith("vault/"))
        self.assertTrue(key2.startswith("vault/"))

    # ------------------------------------------------------------------
    # Copy flow uses driver layer (integration test with mocked S3 driver)
    # ------------------------------------------------------------------

    def test_copy_reads_from_source_driver_and_writes_to_target_driver(self):
        """
        When the destination bucket uses s3, the copy view must:
        - read content via the source driver
        - write content via the destination (S3) driver, not local storage
        """
        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        alice = User.objects.create_user("alice_s3", password="pass")

        src_bucket = Bucket.objects.create(name="SrcLocal", slug="src-local", owner=alice)
        dst_bucket = Bucket.objects.create(
            name="DstS3", slug="dst-s3", owner=alice,
            storage_backend="s3",
            storage_config={"bucket_name": "fake-s3-bucket"},
        )
        src_file = VaultFile.objects.create(
            owner=alice,
            title="doc",
            key="doc",
            file=SimpleUploadedFile("doc.txt", b"file bytes"),
            file_type="text",
            bucket=src_bucket,
        )

        mock_src = MagicMock()
        mock_src.read.return_value = b"file bytes"
        mock_dst = MagicMock()
        mock_dst.save.return_value = "vault/doc_abc1.txt"

        def fake_driver(bucket):
            return mock_src if bucket.pk == src_bucket.pk else mock_dst

        client = Client()
        client.login(username="alice_s3", password="pass")
        copy_url = reverse("vault:copy_files", kwargs={"source_slug": src_bucket.slug})

        with patch("toto.vault.views.get_bucket_storage", side_effect=fake_driver):
            response = client.post(copy_url, {
                "files": [src_file.pk],
                "destination_bucket": dst_bucket.pk,
            })

        self.assertEqual(response.status_code, 302)
        mock_src.read.assert_called_once_with(src_file.file.name)
        mock_dst.save.assert_called_once()
        mock_src.save.assert_not_called()

        copied = VaultFile.objects.filter(bucket=dst_bucket).first()
        self.assertIsNotNone(copied)
        self.assertEqual(copied.file.name, "vault/doc_abc1.txt")

    # ------------------------------------------------------------------
    # Bucket model — new fields
    # ------------------------------------------------------------------

    def test_bucket_storage_backend_defaults_to_local(self):
        user = User.objects.create_user("bk_default", password="pass")
        bucket = Bucket.objects.create(name="DefaultBucket", slug="default-bucket", owner=user)
        self.assertEqual(bucket.storage_backend, "local")

    def test_bucket_storage_config_defaults_to_empty_dict(self):
        user = User.objects.create_user("bk_cfg", password="pass")
        bucket = Bucket.objects.create(name="CfgBucket", slug="cfg-bucket", owner=user)
        self.assertEqual(bucket.storage_config, {})

    def test_bucket_storage_config_roundtrips_json(self):
        user = User.objects.create_user("bk_json", password="pass")
        cfg = {
            "bucket_name": "my-ovh-bucket",
            "endpoint_url": "https://s3.gra.perf.cloud.ovh.net",
            "region_name": "gra",
            "prefix": "project/",
            "use_ssl": True,
            "addressing_style": "path",
        }
        bucket = Bucket.objects.create(
            name="JsonBucket", slug="json-bucket", owner=user,
            storage_backend="s3", storage_config=cfg,
        )
        bucket.refresh_from_db()
        self.assertEqual(bucket.storage_config["bucket_name"], "my-ovh-bucket")
        self.assertEqual(bucket.storage_config["endpoint_url"], "https://s3.gra.perf.cloud.ovh.net")
        self.assertEqual(bucket.storage_config["prefix"], "project/")
        self.assertTrue(bucket.storage_config["use_ssl"])

    # ------------------------------------------------------------------
    # S3 driver — prefix and key normalisation
    # ------------------------------------------------------------------

    def test_s3_driver_prefix_defaults_to_vault(self):
        from toto.vault.storage_backends import S3CompatibleVaultStorageDriver

        driver = S3CompatibleVaultStorageDriver({"bucket_name": "b"})
        driver._client = MagicMock()
        key = driver.save("file.txt", b"x")
        self.assertTrue(key.startswith("vault/"), key)

    def test_s3_driver_custom_prefix(self):
        from toto.vault.storage_backends import S3CompatibleVaultStorageDriver

        driver = S3CompatibleVaultStorageDriver({"bucket_name": "b", "prefix": "docs/archive/"})
        driver._client = MagicMock()
        key = driver.save("report.pdf", b"data")
        self.assertTrue(key.startswith("docs/archive/"), key)

    def test_s3_driver_empty_prefix(self):
        from toto.vault.storage_backends import S3CompatibleVaultStorageDriver

        driver = S3CompatibleVaultStorageDriver({"bucket_name": "b", "prefix": ""})
        driver._client = MagicMock()
        key = driver.save("file.txt", b"x")
        self.assertFalse(key.startswith("/"), key)
        self.assertIn("file", key)

    def test_s3_driver_safe_filename_strips_path_components(self):
        from toto.vault.storage_backends import S3CompatibleVaultStorageDriver

        driver = S3CompatibleVaultStorageDriver({"bucket_name": "b", "prefix": "v/"})
        driver._client = MagicMock()
        # A full local path is passed as the hint — only the basename should appear in the key
        key = driver.save("vault/files/subdir/report.pdf", b"x")
        self.assertNotIn("vault/files/subdir", key)
        self.assertIn("report", key)

    def test_s3_driver_safe_filename_replaces_unsafe_chars(self):
        from toto.vault.storage_backends import _safe_filename

        self.assertEqual(_safe_filename("my file (1).txt"), "my_file__1_.txt")
        self.assertEqual(_safe_filename("résumé.pdf"), "r_sum_.pdf")
        self.assertEqual(_safe_filename("a/b/c.txt"), "c.txt")   # basename strips path
        self.assertEqual(_safe_filename(""), "file")              # empty → fallback

    def test_s3_driver_missing_bucket_name_raises(self):
        from toto.vault.storage_backends import S3CompatibleVaultStorageDriver

        driver = S3CompatibleVaultStorageDriver({})
        driver._client = MagicMock()
        with self.assertRaises(ValueError):
            driver.save("file.txt", b"x")

    def test_s3_driver_ssl_disabled_passes_flag(self):
        import sys
        from toto.vault.storage_backends import S3CompatibleVaultStorageDriver

        _, mock_session, modules = self._fake_boto3_modules()
        driver = S3CompatibleVaultStorageDriver({
            "bucket_name": "b",
            "endpoint_url": "http://minio:9000",
            "use_ssl": False,
        })

        with patch.dict(sys.modules, modules):
            driver._build_client()

        _, call_kwargs = mock_session.client.call_args
        self.assertIs(call_kwargs.get("use_ssl"), False)

    # ------------------------------------------------------------------
    # S3 driver — exists / delete
    # ------------------------------------------------------------------

    def test_s3_driver_exists_true(self):
        from toto.vault.storage_backends import S3CompatibleVaultStorageDriver

        driver = S3CompatibleVaultStorageDriver({"bucket_name": "b"})
        driver._client = MagicMock()  # head_object returns without raising → exists

        self.assertTrue(driver.exists("vault/key.txt"))
        driver._client.head_object.assert_called_once_with(Bucket="b", Key="vault/key.txt")

    def test_s3_driver_exists_false_on_exception(self):
        from toto.vault.storage_backends import S3CompatibleVaultStorageDriver

        driver = S3CompatibleVaultStorageDriver({"bucket_name": "b"})
        mock_client = MagicMock()
        mock_client.head_object.side_effect = Exception("NoSuchKey")
        driver._client = mock_client

        self.assertFalse(driver.exists("vault/missing.txt"))

    def test_s3_driver_delete_calls_delete_object(self):
        from toto.vault.storage_backends import S3CompatibleVaultStorageDriver

        driver = S3CompatibleVaultStorageDriver({"bucket_name": "b"})
        driver._client = MagicMock()
        driver.delete("vault/old_file.txt")

        driver._client.delete_object.assert_called_once_with(
            Bucket="b", Key="vault/old_file.txt"
        )

    def test_s3_driver_delete_swallows_exception(self):
        from toto.vault.storage_backends import S3CompatibleVaultStorageDriver

        driver = S3CompatibleVaultStorageDriver({"bucket_name": "b"})
        mock_client = MagicMock()
        mock_client.delete_object.side_effect = Exception("network error")
        driver._client = mock_client

        driver.delete("vault/file.txt")  # must not raise

    # ------------------------------------------------------------------
    # Copy flow — metadata and file_size_bytes
    # ------------------------------------------------------------------

    def test_copy_preserves_all_metadata_fields(self):
        """Copied VaultFile inherits title, file_type, content_hash, notes, is_encrypted, is_public."""
        Platform.objects.create(site_name="Meta", author="T", publication_year=2024, active=True)
        user = User.objects.create_user("meta_user", password="pass")
        src = Bucket.objects.create(name="MetaSrc", slug="meta-src", owner=user)
        dst = Bucket.objects.create(name="MetaDst", slug="meta-dst", owner=user)

        orig = VaultFile.objects.create(
            owner=user, title="original title", key="orig",
            file=SimpleUploadedFile("orig.txt", b"abc"),
            file_type="text", content_hash="deadbeef",
            notes="some notes", is_encrypted=False, is_public=True,
            bucket=src,
        )

        client = Client()
        client.login(username="meta_user", password="pass")
        client.post(
            reverse("vault:copy_files", kwargs={"source_slug": src.slug}),
            {"files": [orig.pk], "destination_bucket": dst.pk},
        )

        copy = VaultFile.objects.get(bucket=dst)
        self.assertEqual(copy.title, "original title")
        self.assertEqual(copy.file_type, "text")
        self.assertEqual(copy.content_hash, "deadbeef")
        self.assertEqual(copy.notes, "some notes")
        self.assertFalse(copy.is_encrypted)
        self.assertTrue(copy.is_public)
        self.assertEqual(copy.owner, user)

    def test_copy_sets_file_size_bytes_from_content_length(self):
        """file_size_bytes on the copy equals the byte length of the content read."""
        Platform.objects.create(site_name="Size", author="T", publication_year=2024, active=True)
        user = User.objects.create_user("size_user", password="pass")
        src = Bucket.objects.create(name="SizeSrc", slug="size-src", owner=user)
        dst = Bucket.objects.create(name="SizeDst", slug="size-dst", owner=user)

        content = b"exactly 22 bytes!!!!!"
        orig = VaultFile.objects.create(
            owner=user, title="sized", key="sized",
            file=SimpleUploadedFile("sized.txt", content),
            file_type="text", bucket=src,
        )

        client = Client()
        client.login(username="size_user", password="pass")
        client.post(
            reverse("vault:copy_files", kwargs={"source_slug": src.slug}),
            {"files": [orig.pk], "destination_bucket": dst.pk},
        )

        copy = VaultFile.objects.get(bucket=dst)
        self.assertEqual(copy.file_size_bytes, len(content))

    # ------------------------------------------------------------------
    # Backward compatibility — existing local files still open normally
    # ------------------------------------------------------------------

    def test_local_files_open_via_default_storage_after_migration(self):
        """VaultFile.file.open() still works for local-storage files after the migration."""
        user = User.objects.create_user("compat_user", password="pass")
        bucket = Bucket.objects.create(name="Compat", slug="compat", owner=user)
        # storage_backend defaults to "local" — existing files are unaffected
        self.assertEqual(bucket.storage_backend, "local")

        vf = VaultFile.objects.create(
            owner=user, title="compat file", key="compat-file",
            file=SimpleUploadedFile("compat.txt", b"backward compat"),
            file_type="text", bucket=bucket,
        )
        vf.file.open("rb")
        data = vf.file.read()
        vf.file.close()
        self.assertEqual(data, b"backward compat")


# ===========================================================================
# VaultPlayPlugin registry tests
# ===========================================================================

class VaultPlayPluginRegistryTests(TestCase):
    def test_video_plugin_registered_when_vod_installed(self):
        from django.apps import apps
        if not apps.is_installed("toto.vod"):
            self.skipTest("toto.vod not installed")
        plugin = VaultPlayPlugin.for_file_type("video")
        self.assertIsNotNone(plugin)

    def test_video_plugin_returns_play_url(self):
        from django.apps import apps
        if not apps.is_installed("toto.vod"):
            self.skipTest("toto.vod not installed")
        plugin = VaultPlayPlugin.for_file_type("video")
        user = User.objects.create_user("plugin_owner", password="pass")
        bucket = Bucket.objects.create(name="plugin-bucket", slug="plugin-bucket", owner=user)
        vf = VaultFile.objects.create(
            owner=user, title="clip.mp4", file_type="video",
            file=SimpleUploadedFile("clip.mp4", b"data"), bucket=bucket,
        )
        url = plugin.get_play_url(vf)
        self.assertIn(str(vf.pk), url)
        self.assertIn("/vod/", url)

    def test_audio_plugin_registered_when_vod_installed(self):
        from django.apps import apps
        if not apps.is_installed("toto.vod"):
            self.skipTest("toto.vod not installed")
        plugin = VaultPlayPlugin.for_file_type("audio")
        self.assertIsNotNone(plugin)

    def test_no_plugin_for_pdf(self):
        plugin = VaultPlayPlugin.for_file_type("pdf")
        self.assertIsNone(plugin)

    def test_no_plugin_for_text(self):
        plugin = VaultPlayPlugin.for_file_type("text")
        self.assertIsNone(plugin)

    def test_no_plugin_for_image(self):
        plugin = VaultPlayPlugin.for_file_type("image")
        self.assertIsNone(plugin)


class FlatItemsPlayUrlTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("flat_owner", password="pass")
        self.bucket = Bucket.objects.create(name="flat-bucket", slug="flat-bucket", owner=self.user)

    def _make_file(self, file_type, is_encrypted=False):
        vf = VaultFile(
            owner=self.user, title=f"file.{file_type}",
            file_type=file_type, bucket=self.bucket,
            is_public=True, is_encrypted=is_encrypted,
        )
        vf.file.save(f"file.{file_type}", SimpleUploadedFile(f"f.{file_type}", b"x"), save=False)
        vf.file_size_bytes = 1
        vf.save()
        return vf

    def _flat_items_for(self, files):
        from toto.vault.views import PublicFileListView
        view = PublicFileListView()
        return view._build_flat_items([], files, {})

    def test_video_file_has_play_url_when_vod_installed(self):
        from django.apps import apps
        if not apps.is_installed("toto.vod"):
            self.skipTest("toto.vod not installed")
        vf = self._make_file("video")
        items = self._flat_items_for([vf])
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["play_url"])

    def test_pdf_file_has_empty_play_url(self):
        vf = self._make_file("pdf")
        items = self._flat_items_for([vf])
        self.assertEqual(items[0]["play_url"], "")

    def test_encrypted_video_has_empty_play_url(self):
        from django.apps import apps
        if not apps.is_installed("toto.vod"):
            self.skipTest("toto.vod not installed")
        vf = self._make_file("video", is_encrypted=True)
        items = self._flat_items_for([vf])
        self.assertEqual(items[0]["play_url"], "")


class ZipFileTypeTests(TestCase):
    def test_detect_type_from_extension(self):
        self.assertEqual(VaultFile.detect_type("", "archive.zip"), "zip")

    def test_detect_type_from_mime(self):
        self.assertEqual(VaultFile.detect_type("application/zip", "x"), "zip")
        self.assertEqual(VaultFile.detect_type("application/x-zip-compressed", "x"), "zip")

    def test_zip_is_a_valid_choice(self):
        self.assertIn("zip", dict(VaultFile.FILE_TYPES))


class ZipArchiveHelperTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_media = tempfile.mkdtemp()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self._override = override_settings(MEDIA_ROOT=self.temp_media)
        self._override.enable()
        self.alice = User.objects.create_user("zip_alice", password="pass")
        self.bucket = Bucket.objects.create(name="Z", slug="z-bucket", owner=self.alice)
        self.docs = VaultDirectory.objects.create(name="Docs", bucket=self.bucket, owner=self.alice)
        self.sub = VaultDirectory.objects.create(name="Sub", bucket=self.bucket, parent=self.docs, owner=self.alice)

    def tearDown(self):
        self._override.disable()

    def _file(self, title, key, directory, *, encrypted=False, body=b"data"):
        return VaultFile.objects.create(
            owner=self.alice, title=title, key=key,
            file=SimpleUploadedFile(f"{key}.txt", body),
            file_type="text", bucket=self.bucket, directory=directory,
            is_encrypted=encrypted,
        )

    def test_zip_preserves_relative_structure_and_excludes_encrypted(self):
        import zipfile
        from toto.vault.archive import zip_files_to_vault_file

        top = self._file("report.txt", "report", self.docs)
        nested = self._file("nested.txt", "nested", self.sub)
        secret = self._file("secret.txt", "secret", self.docs, encrypted=True)

        vf, n = zip_files_to_vault_file(
            self.alice, self.docs, self.docs,
            [top.pk, nested.pk, secret.pk], "Docs.zip",
        )
        self.assertEqual(vf.file_type, "zip")
        self.assertEqual(vf.directory_id, self.docs.pk)
        self.assertEqual(vf.title, "Docs.zip")
        self.assertEqual(n, 2)  # encrypted file excluded

        with vf.file.open("rb") as fh:
            names = set(zipfile.ZipFile(fh).namelist())
        self.assertEqual(names, {"report.txt", "Sub/nested.txt"})

    def test_zip_no_eligible_files_raises(self):
        from toto.vault.archive import zip_files_to_vault_file
        secret = self._file("secret.txt", "secret", self.docs, encrypted=True)
        with self.assertRaises(ValueError):
            zip_files_to_vault_file(self.alice, self.docs, None, [secret.pk], "x.zip")

    def test_output_name_gets_zip_extension_and_root_target(self):
        from toto.vault.archive import zip_files_to_vault_file
        f = self._file("a.txt", "a", self.docs)
        vf, _ = zip_files_to_vault_file(self.alice, self.docs, None, [f.pk], "myarchive")
        self.assertTrue(vf.title.endswith(".zip"))
        self.assertIsNone(vf.directory_id)  # target=None → bucket root


class CreateZipViewTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_media = tempfile.mkdtemp()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self._override = override_settings(MEDIA_ROOT=self.temp_media)
        self._override.enable()
        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.alice = User.objects.create_user("cz_alice", password="pass")
        self.bob = User.objects.create_user("cz_bob", password="pass")
        self.client = Client()
        self.bucket = Bucket.objects.create(name="Z", slug="cz-bucket", owner=self.alice)
        self.docs = VaultDirectory.objects.create(name="Docs", bucket=self.bucket, owner=self.alice)
        self.f = VaultFile.objects.create(
            owner=self.alice, title="a.txt", key="a",
            file=SimpleUploadedFile("a.txt", b"x"), file_type="text",
            bucket=self.bucket, directory=self.docs,
        )

    def tearDown(self):
        self._override.disable()

    def _skip_if_no_workflows(self):
        from django.apps import apps
        if not apps.is_installed("toto.workflows"):
            self.skipTest("workflows not installed")

    def test_owner_queues_when_celery_available(self):
        self._skip_if_no_workflows()
        self.client.login(username="cz_alice", password="pass")
        with patch("toto.celery_utils.celery_available", return_value=True), \
             patch("toto.workflows.tasks.start_workflow_run_task") as task:
            resp = self.client.post(reverse("vault:create_zip"), {
                "source_directory_id": self.docs.pk,
                "target_directory_id": "",
                "output_name": "Docs.zip",
                "file_ids": [self.f.pk],
            })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "queued")
        task.delay.assert_called_once_with(data["workflow_run_id"])
        from toto.workflows.models import WorkflowRun
        self.assertTrue(WorkflowRun.objects.filter(pk=data["workflow_run_id"]).exists())
        # async path: nothing zipped inline yet
        self.assertFalse(VaultFile.objects.filter(bucket=self.bucket, file_type="zip").exists())

    def test_owner_runs_inline_without_celery(self):
        self._skip_if_no_workflows()
        self.client.login(username="cz_alice", password="pass")
        with patch("toto.celery_utils.celery_available", return_value=False):
            resp = self.client.post(reverse("vault:create_zip"), {
                "source_directory_id": self.docs.pk,
                "target_directory_id": "",
                "output_name": "Docs.zip",
                "file_ids": [self.f.pk],
            })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")
        # inline path actually produced the archive
        zips = VaultFile.objects.filter(bucket=self.bucket, file_type="zip")
        self.assertEqual(zips.count(), 1)
        self.assertEqual(zips.first().title, "Docs.zip")

    def test_non_owner_denied(self):
        self.client.login(username="cz_bob", password="pass")
        resp = self.client.post(reverse("vault:create_zip"), {
            "source_directory_id": self.docs.pk,
            "file_ids": [self.f.pk],
        })
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(VaultFile.objects.filter(bucket=self.bucket, file_type="zip").exists())

    def test_no_files_selected(self):
        self._skip_if_no_workflows()
        self.client.login(username="cz_alice", password="pass")
        resp = self.client.post(reverse("vault:create_zip"), {
            "source_directory_id": self.docs.pk,
            "file_ids": [],
        })
        self.assertEqual(resp.status_code, 400)


class GatewayMultiUploadTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_media = tempfile.mkdtemp()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        from toto.vault.models import FileGateway
        self._override = override_settings(MEDIA_ROOT=self.temp_media)
        self._override.enable()
        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.alice = User.objects.create_user("gw_alice", password="pass")
        self.client = Client()
        self.bucket = Bucket.objects.create(name="GW", slug="gw-bucket", owner=self.alice)
        self.dir = VaultDirectory.objects.create(name="Inbox", bucket=self.bucket, owner=self.alice)
        # max_file_size is in KB.
        self.gateway = FileGateway.objects.create(directory=self.dir, name="Inbox GW", max_file_size=10 * 1024)
        self.client.login(username="gw_alice", password="pass")

    def tearDown(self):
        self._override.disable()

    def _url(self):
        return reverse("vault:gateway_upload", kwargs={"dir_pk": self.dir.pk})

    def test_multiple_files_uploaded_in_one_request(self):
        resp = self.client.post(self._url(), {"file": [
            SimpleUploadedFile("a.txt", b"aaa"),
            SimpleUploadedFile("b.txt", b"bbb"),
        ]})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["results"]), 2)
        self.assertEqual(data["errors"], [])
        self.assertEqual(VaultFile.objects.filter(bucket=self.bucket).count(), 2)

    def test_oversized_file_skipped_others_succeed(self):
        self.gateway.max_file_size = 1  # 1 KB limit
        self.gateway.save()
        resp = self.client.post(self._url(), {"file": [
            SimpleUploadedFile("small.txt", b"x"),
            SimpleUploadedFile("big.txt", b"x" * 2048),  # 2 KB > 1 KB
        ]})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual([r["title"] for r in data["results"]], ["small.txt"])
        self.assertEqual(len(data["errors"]), 1)
        self.assertIn("big.txt", data["errors"][0])
        self.assertEqual(VaultFile.objects.filter(bucket=self.bucket).count(), 1)

    def test_all_oversized_returns_400(self):
        self.gateway.max_file_size = 1
        self.gateway.save()
        resp = self.client.post(self._url(), {"file": [SimpleUploadedFile("big.txt", b"x" * 2048)]})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["results"], [])
        self.assertEqual(VaultFile.objects.filter(bucket=self.bucket).count(), 0)

    def test_no_file_returns_400(self):
        resp = self.client.post(self._url(), {})
        self.assertEqual(resp.status_code, 400)

    def test_duplicate_filenames_get_unique_keys(self):
        # Two files with the same name in one batch must both succeed with
        # distinct keys — not 500 with an HTML page (which broke client JSON parse).
        resp = self.client.post(self._url(), {"file": [
            SimpleUploadedFile("dup.txt", b"one"),
            SimpleUploadedFile("dup.txt", b"two"),
        ]})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["results"]), 2)
        self.assertEqual(data["errors"], [])
        keys = set(VaultFile.objects.filter(bucket=self.bucket).values_list("key", flat=True))
        self.assertEqual(len(keys), 2)  # e.g. {"dup", "dup-1"}

    def test_reupload_existing_name_succeeds(self):
        VaultFile.objects.create(
            owner=self.alice, title="doc.txt", key="doc",
            file=SimpleUploadedFile("doc.txt", b"x"), file_type="text",
            bucket=self.bucket, directory=self.dir,
        )
        resp = self.client.post(self._url(), {"file": SimpleUploadedFile("doc.txt", b"y")})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["results"]), 1)


class EncryptWorkflowViewTests(TestCase):
    """Encryption runs as its own 'vault-encrypt' workflow (Celery-backed), and the
    password is NEVER persisted to the run data — only passed as a transient arg."""

    @classmethod
    def setUpClass(cls):
        cls.temp_media = tempfile.mkdtemp()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self._override = override_settings(MEDIA_ROOT=self.temp_media, VAULT_ENCRYPT_ASYNC=True)
        self._override.enable()
        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.alice = User.objects.create_user("enc_alice", password="pass")
        self.client = Client()
        self.bucket = Bucket.objects.create(name="E", slug="enc-bucket", owner=self.alice)
        self.f = VaultFile.objects.create(
            owner=self.alice, title="secret.txt", key="secret",
            file=SimpleUploadedFile("secret.txt", b"hello"), file_type="text",
            bucket=self.bucket, is_public=True,
        )

    def tearDown(self):
        self._override.disable()

    def _skip_if_no_workflows(self):
        from django.apps import apps
        if not apps.is_installed("toto.workflows"):
            self.skipTest("workflows not installed")

    _PW = "hunter2-super-secret"

    def test_queues_workflow_without_persisting_password(self):
        self._skip_if_no_workflows()
        self.client.login(username="enc_alice", password="pass")
        with patch("toto.celery_utils.celery_available", return_value=True), \
             patch("toto.vault.tasks.encrypt_workflow_run") as task:
            resp = self.client.post(reverse("vault:encrypt_file"), {
                "file_pk": self.f.pk, "password": self._PW,
            })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["async"])
        run_id = data["workflow_run_id"]
        task.delay.assert_called_once_with(run_id, self._PW, None)
        from toto.workflows.models import WorkflowRun
        run = WorkflowRun.objects.get(pk=run_id)
        self.assertEqual(run.workflow.slug, "vault-encrypt")
        self.assertEqual(run.input_data["data"]["file_pk"], self.f.pk)
        # Security: the password must never land in the persisted run input.
        self.assertNotIn(self._PW, json.dumps(run.input_data))
        # Async path: nothing encrypted inline yet.
        self.f.refresh_from_db()
        self.assertFalse(self.f.is_encrypted)

    def test_runs_inline_and_completes_without_persisting_password(self):
        self._skip_if_no_workflows()
        self.client.login(username="enc_alice", password="pass")

        def _fake_encrypt(vf, password=None, owner_password=None):
            vf.is_encrypted = True
            vf.save(update_fields=["is_encrypted"])

        with patch("toto.celery_utils.celery_available", return_value=False), \
             patch("toto.vault.models.VaultFile.encrypt", _fake_encrypt):
            resp = self.client.post(reverse("vault:encrypt_file"), {
                "file_pk": self.f.pk, "password": self._PW,
            })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"] and data["async"])
        run_id = data["workflow_run_id"]

        self.f.refresh_from_db()
        self.assertTrue(self.f.is_encrypted)

        from toto.workflows.models import WorkflowNodeRun, WorkflowRun
        run = WorkflowRun.objects.get(pk=run_id)
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        node_run = WorkflowNodeRun.objects.get(workflow_run=run)
        self.assertEqual(node_run.status, WorkflowNodeRun.COMPLETED)
        self.assertEqual(node_run.output_data["data"]["vault_file_id"], self.f.pk)

        # Security: no copy of the password anywhere in the persisted run rows.
        self.assertNotIn(self._PW, json.dumps(run.input_data))
        self.assertNotIn(self._PW, json.dumps(node_run.input_data))
        self.assertNotIn(self._PW, json.dumps(run.output_data))

        # Status poll reports the terminal run + the encrypted file id.
        status = self.client.get(reverse("vault:encrypt_status"), {"run_id": run_id}).json()
        self.assertTrue(status["is_terminal"])
        self.assertTrue(status["ok"])
        self.assertEqual(status["vault_file_id"], self.f.pk)

    def test_already_encrypted_rejected(self):
        self.f.is_encrypted = True
        self.f.save(update_fields=["is_encrypted"])
        self.client.login(username="enc_alice", password="pass")
        resp = self.client.post(reverse("vault:encrypt_file"), {
            "file_pk": self.f.pk, "password": self._PW,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])

    def test_status_poll_not_owned_is_404(self):
        self._skip_if_no_workflows()
        from toto.vault.views import EncryptFileView
        from toto.workflows.models import WorkflowRun
        wf = EncryptFileView._ensure_workflow()
        run = WorkflowRun.objects.create(
            workflow=wf, input_data={"data": {"file_pk": self.f.pk, "owner_id": self.alice.id}},
        )
        mallory = User.objects.create_user("enc_mallory", password="pass")
        self.client.force_login(mallory)
        resp = self.client.get(reverse("vault:encrypt_status"), {"run_id": run.id})
        self.assertEqual(resp.status_code, 404)


class EncryptedEditLockTests(TestCase):
    """Encrypted files are locked out of editors: the vault UI hides the Edit button
    and a direct editor URL returns the 403 'decrypt first' page."""

    @classmethod
    def setUpClass(cls):
        cls.temp_media = tempfile.mkdtemp()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self._override = override_settings(MEDIA_ROOT=self.temp_media)
        self._override.enable()
        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.user = User.objects.create_user("lock_owner", password="pass")
        self.client = Client()
        self.bucket = Bucket.objects.create(name="L", slug="lock-bucket", owner=self.user)

    def tearDown(self):
        self._override.disable()

    def _make_file(self, is_encrypted):
        vf = VaultFile(
            owner=self.user, title="doc.txt", key="doc",
            file_type="text", bucket=self.bucket, is_public=True, is_encrypted=is_encrypted,
        )
        vf.file.save("doc.txt", SimpleUploadedFile("doc.txt", b"hello"), save=False)
        vf.file_size_bytes = 5
        vf.save()
        return vf

    def _editor_url(self, vf):
        from toto.vault.views import PublicFileListView
        items = PublicFileListView()._build_flat_items([], [vf], {})
        return items[0]["editor_url"]

    def test_editor_url_present_when_plaintext(self):
        from django.apps import apps
        if not apps.is_installed("toto.editor"):
            self.skipTest("editor not installed")
        self.assertTrue(self._editor_url(self._make_file(is_encrypted=False)))

    def test_editor_url_blank_when_encrypted(self):
        # True regardless of which editor apps are installed — the guard is in vault.
        self.assertEqual(self._editor_url(self._make_file(is_encrypted=True)), "")

    def test_editor_view_blocks_encrypted(self):
        from django.apps import apps
        if not apps.is_installed("toto.editor"):
            self.skipTest("editor not installed")
        vf = self._make_file(is_encrypted=True)
        self.client.login(username="lock_owner", password="pass")
        resp = self.client.get(reverse("editor:text_display", args=[vf.pk]))
        self.assertEqual(resp.status_code, 403)

    def test_editor_save_blocks_encrypted(self):
        from django.apps import apps
        if not apps.is_installed("toto.editor"):
            self.skipTest("editor not installed")
        vf = self._make_file(is_encrypted=True)
        self.client.login(username="lock_owner", password="pass")
        resp = self.client.post(reverse("editor:text_save", args=[vf.pk]), {"content": "new"})
        self.assertEqual(resp.status_code, 403)

    def test_plaintext_editor_view_ok(self):
        from django.apps import apps
        if not apps.is_installed("toto.editor"):
            self.skipTest("editor not installed")
        vf = self._make_file(is_encrypted=False)
        self.client.login(username="lock_owner", password="pass")
        resp = self.client.get(reverse("editor:text_display", args=[vf.pk]))
        self.assertEqual(resp.status_code, 200)