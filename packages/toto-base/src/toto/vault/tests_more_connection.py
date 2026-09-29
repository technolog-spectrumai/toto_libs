"""The shareable, credential-free connection URL of a bucket
(``BucketConnectionSpec.from_bucket(...).to_url()`` and its owner-only door).
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.vault.connection import BucketConnectionSpec
from toto.vault.models import Bucket, StorageProvider

User = get_user_model()


class SpecTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.ovh = StorageProvider.objects.create(
            name="ovh", display_name="OVH",
            endpoint_url_template="https://s3.{region}.io.cloud.ovh.net", default_region="gra")

    def bucket(self, slug, **kwargs):
        return Bucket.objects.create(name=slug, slug=slug, owner=self.owner, **kwargs)

    def test_a_local_bucket_is_named_by_its_slug(self):
        self.assertEqual(self.bucket("photos").get_connection_url(), "local:///photos")

    def test_a_provider_bucket_resolves_its_endpoint_from_the_region(self):
        b = self.bucket("s3a", storage_backend="s3", provider=self.ovh,
                        storage_config={"bucket_name": "media", "region_name": "sbg",
                                        "prefix": "vault/"})
        spec = BucketConnectionSpec.from_bucket(b)
        self.assertEqual(spec.endpoint_url, "https://s3.sbg.io.cloud.ovh.net")
        self.assertEqual(spec.to_url(), "s3+ovh://media@s3.sbg.io.cloud.ovh.net/vault")

    def test_the_providers_default_region_fills_a_blank_one(self):
        b = self.bucket("s3b", storage_backend="s3", provider=self.ovh,
                        storage_config={"bucket_name": "media"})
        self.assertEqual(BucketConnectionSpec.from_bucket(b).endpoint_url,
                         "https://s3.gra.io.cloud.ovh.net")

    def test_an_explicit_endpoint_wins_over_the_provider_template(self):
        b = self.bucket("s3c", storage_backend="s3", provider=self.ovh,
                        storage_config={"bucket_name": "m", "endpoint_url": "https://own.example"})
        self.assertEqual(BucketConnectionSpec.from_bucket(b).to_url(),
                         "s3+ovh://m@own.example/vault")

    def test_plain_aws_carries_no_host_and_a_custom_prefix(self):
        b = self.bucket("s3d", storage_backend="s3",
                        storage_config={"bucket_name": "raw", "prefix": "/deep/path/"})
        self.assertEqual(BucketConnectionSpec.from_bucket(b).to_url(), "s3://raw/deep/path")

    def test_nothing_secret_ever_reaches_the_url(self):
        b = self.bucket("s3e", storage_backend="s3",
                        storage_config={"bucket_name": "raw", "aws_profile": "prod-admin",
                                        "access_key": "AKIA-NOPE"})
        url = BucketConnectionSpec.from_bucket(b).to_url()
        self.assertNotIn("prod-admin", url)
        self.assertNotIn("AKIA", url)

    def test_a_pre_peering_remote_bucket_falls_back_to_its_old_config(self):
        b = Bucket(name="r", slug="r", owner=self.owner, storage_backend="remote_toto",
                   storage_config={"server_url": "http://peer.lan", "bucket_slug": "theirs"})
        spec = BucketConnectionSpec.from_bucket(b)
        self.assertEqual(spec.to_url(), "toto+http://peer.lan/vault/buckets/theirs/")
        https = BucketConnectionSpec(backend="remote_toto", bucket_name="x",
                                     server_url="https://peer.example")
        self.assertEqual(https.to_url(), "toto://peer.example/vault/buckets/x/")

    def test_storage_config_for_import_holds_only_non_secrets(self):
        spec = BucketConnectionSpec(backend="s3", bucket_name="b", endpoint_url="https://e",
                                    region="eu", prefix="p/")
        self.assertEqual(spec.to_storage_config(), {"bucket_name": "b", "prefix": "p/",
                                                    "endpoint_url": "https://e",
                                                    "region_name": "eu"})
        self.assertEqual(BucketConnectionSpec(backend="local", bucket_name="x")
                         .to_storage_config(), {})

    def test_an_unknown_scheme_is_refused(self):
        with self.assertRaises(ValueError):
            BucketConnectionSpec.from_url("ftp://example.org/x")


class ConnectionUrlViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.other = User.objects.create_user("other", password="pw")
        cls.local = Bucket.objects.create(name="L", slug="loc", owner=cls.owner)
        cls.s3 = Bucket.objects.create(name="S", slug="s3", owner=cls.owner,
                                       storage_backend="s3",
                                       storage_config={"bucket_name": "media"})

    def url(self, bucket):
        return reverse("vault:bucket_connection_url", args=[bucket.slug])

    def test_the_owner_gets_the_url_and_its_parts(self):
        self.client.force_login(self.owner)
        payload = self.client.get(self.url(self.s3)).json()
        self.assertEqual(payload, {"url": "s3://media/vault", "backend": "s3", "provider": "",
                                   "bucket_name": "media"})

    def test_someone_elses_bucket_is_404(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url(self.local)).status_code, 404)

    @override_settings(VAULT_EXTERNAL_BUCKETS=False)
    def test_a_host_without_external_buckets_advertises_only_local_ones(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url(self.s3)).status_code, 404)
        self.assertEqual(self.client.get(self.url(self.local)).json()["url"], "local:///loc")
