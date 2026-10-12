"""Object storages the operator configured (2026-10-11): the New bucket
dialog offers them by name and location, never by the company behind them,
asks for no key, and a bucket made in one carries the storage's name.

    manage.py test toto.vault.tests_object_storage
"""
from unittest import mock

from django.contrib.auth.models import User
from django.core.checks import run_checks
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.vault.models import Bucket
from toto.vault.plugins.storage_adapters import (ConfiguredStorageAdapter, configured_storage_faults,
                                                 configured_storages, storage_label)
from toto.vault.storage_adapters import StorageAdapter

STORAGES = [
    {"name": "archive", "location": "Warsaw", "provider": "ovh", "region": "waw",
     "access_key_id": "AKIAARCHIVE", "secret_access_key": "archive-secret"},
    {"name": "offsite", "location": "the office NAS", "provider": "s3",
     "endpoint_url": "https://s3.example.org", "access_key_id": "nas", "secret_access_key": "nas-secret"},
]


@override_settings(VAULT_EXTERNAL_BUCKETS=True, VAULT_BUCKET_KINDS=("local", "object_storage"),
                   VAULT_OBJECT_STORAGES=STORAGES, VAULT_OUTBOUND_ALLOWED_HOSTS=["s3.example.org"])
class ObjectStorageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.root = User.objects.create_superuser("root", password="pw")

    def test_the_kind_is_offered_only_with_storages_configured(self):
        keys = [a.get_key() for a in StorageAdapter.creatable_adapters()]
        self.assertEqual(keys, ["local", "object_storage"])
        with override_settings(VAULT_OBJECT_STORAGES=[]):
            self.assertEqual([a.get_key() for a in StorageAdapter.creatable_adapters()], ["local"])
        with override_settings(VAULT_OBJECT_STORAGES='[{"name": "x", "provider": "aws", "region": "eu-west-1", '
                                                     '"access_key_id": "a", "secret_access_key": "b"}]'):
            self.assertEqual([s["name"] for s in configured_storages()], ["x"])     # JSON, as .env carries it

    def test_the_dialog_shows_names_and_locations_and_asks_for_no_key(self):
        adapter = StorageAdapter.for_key("object_storage")
        fields = {f["name"]: f for f in adapter.fields()}
        self.assertEqual(sorted(fields), ["bucket_name", "prefix", "storage"])
        self.assertEqual(fields["storage"]["choices"],
                         [("archive", "archive — Warsaw"), ("offsite", "offsite — the office NAS")])
        self.assertEqual(adapter.secret_field_names(), set())
        client = Client()
        client.force_login(self.root)
        page = client.get(reverse("vault:manage")).content.decode()
        self.assertIn("archive — Warsaw", page)
        for company in ("OVH", "Amazon", "AKIAARCHIVE", "archive-secret"):
            self.assertNotIn(company, page)

    def test_validate_takes_the_keys_from_the_configuration(self):
        adapter = StorageAdapter.for_key("object_storage")
        config, secret = adapter.validate({"storage": "archive", "bucket_name": "files", "prefix": ""})
        self.assertEqual(config, {"storage": "archive", "bucket_name": "files", "prefix": "vault/",
                                  "region_name": "waw"})
        self.assertEqual(secret, {"aws_access_key_id": "AKIAARCHIVE",
                                  "aws_secret_access_key": "archive-secret"})
        config, _secret = adapter.validate({"storage": "offsite", "bucket_name": "files"})
        self.assertEqual(config["endpoint_url"], "https://s3.example.org")
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError) as refused:
            adapter.validate({"storage": "nowhere", "bucket_name": "files"})
        self.assertIn("storage", refused.exception.message_dict)

    def test_a_bucket_is_saved_only_after_the_storage_answers(self):
        client = Client()
        client.force_login(self.root)
        with mock.patch.object(ConfiguredStorageAdapter, "probe_candidate",
                               return_value=(False, "refused these credentials (403)")):
            client.post(reverse("vault:manage_create"),
                        {"kind": "object_storage", "name": "Archive", "owner": self.root.pk,
                         "storage": "archive", "bucket_name": "files"})
        self.assertFalse(Bucket.objects.filter(slug="archive").exists())
        with mock.patch.object(ConfiguredStorageAdapter, "probe_candidate", return_value=(True, "ok")):
            client.post(reverse("vault:manage_create"),
                        {"kind": "object_storage", "name": "Archive", "owner": self.root.pk,
                         "storage": "archive", "bucket_name": "files"})
        bucket = Bucket.objects.get(slug="archive")
        self.assertEqual(bucket.storage_backend, "s3")
        self.assertEqual(bucket.storage_config, {"storage": "archive", "bucket_name": "files",
                                                 "region_name": "waw", "prefix": "vault/"})
        self.assertEqual(bucket.provider.name, "ovh")
        self.assertIs(StorageAdapter.for_bucket(bucket).__class__, ConfiguredStorageAdapter)
        self.assertEqual(StorageAdapter.for_bucket(bucket).target(bucket), "files/vault/ · archive — Warsaw")
        self.assertTrue(bucket.secret.hint)          # the keys, sealed, from the configuration

    def test_faults_are_a_system_check(self):
        self.assertEqual(configured_storage_faults(), [])
        with override_settings(VAULT_OBJECT_STORAGES=[
                {"name": "a", "provider": "ovh", "access_key_id": "k", "secret_access_key": "s"},
                {"name": "a", "provider": "what"}]):
            faults = configured_storage_faults()
            self.assertIn("VAULT_OBJECT_STORAGES[0] (a): region is required for ovh", faults)
            self.assertIn("VAULT_OBJECT_STORAGES[1] (a): the name is used twice", faults)
            self.assertIn("VAULT_OBJECT_STORAGES[1] (a): provider is aws, ovh or s3", faults)
            self.assertIn("VAULT_OBJECT_STORAGES[1] (a): access_key_id is missing", faults)
            self.assertTrue([e for e in run_checks() if e.id == "vault.E010"])
        self.assertEqual(storage_label({"name": "n"}), "n")
