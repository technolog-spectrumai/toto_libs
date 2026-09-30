"""Bucket kinds, their custody and their deletion (2026-09-30).

The foundation Storage → Management builds on: the adapter registry
(``storage_adapters`` + ``plugins/storage_adapters``), S3 keys sealed under
FIELD_ENCRYPTION_KEY (``models.BucketSecret``) and opened per use by the
driver factory, ownerless buckets (``Bucket.owner`` is SET_NULL), and the
purge that deletes a bucket (``bucket_lifecycle``).

S3 is faked the way ``tests.py`` fakes it (boto3 / botocore swapped in
``sys.modules``); another Zenobia is the loopback of ``tests_mirror`` — one
database, the real peer views behind the client.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.vault.tests_storage_adapters
"""

import base64
import json
import os
import socket
import sys
import tempfile
from datetime import timedelta
from unittest import mock
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import ProtectedError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from . import bucket_lifecycle, mirror
from .models import (
    Bucket,
    BucketClearance,
    BucketClosed,
    BucketSecret,
    FileOrigin,
    StorageBackend,
    VaultDirectory,
    VaultFile,
    field_key_configured,
    personal_bucket,
)
from .peering import BucketGrant, BucketPeer, pairing_code_for
from .storage_adapters import StorageAdapter
from .tests_mirror import LoopbackHttp

User = get_user_model()

_MEDIA = tempfile.mkdtemp(prefix="vault-adapters-")

#: A key this test process "keeps" (VAULT_FIELD_KEY_PERSISTENT says so).
PERSISTENT = dict(VAULT_FIELD_KEY_PERSISTENT=True)

SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
KEY_ID = "AKIAIOSFODNN7EXAMPLE"


def fake_boto3(head_error=None):
    """(boto3 module mock, session mock, client mock, sys.modules patch)."""
    client = MagicMock()
    if head_error is not None:
        client.head_bucket.side_effect = head_error
    session = MagicMock()
    session.client.return_value = client
    boto3 = MagicMock()
    boto3.Session.return_value = session
    config = MagicMock()
    config.Config = MagicMock(return_value=MagicMock())
    return boto3, session, client, {"boto3": boto3, "botocore": MagicMock(),
                                     "botocore.config": config}


def public_dns(*args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("51.68.10.20", 0))]


def private_dns(*args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]


class NoHttp:
    """A peer transport that must never be used."""

    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        raise AssertionError(f"the other host was contacted: {method} {url}")


def audit_actions():
    from toto.audit.models import AuditRecord

    return list(AuditRecord.objects.filter(action__startswith="VAULT.BUCKET.")
                .order_by("sequence").values_list("action", flat=True))


def audit_dump() -> str:
    from toto.audit.models import AuditRecord

    return json.dumps(list(AuditRecord.objects.values("metadata", "changes",
                                                       "object_description")), default=str)


@override_settings(MEDIA_ROOT=_MEDIA, VAULT_EXTERNAL_BUCKETS=True)
class Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = User.objects.create_superuser("root", password="x")
        cls.owner = User.objects.create_user("olga", password="x")
        cls.stranger = User.objects.create_user("stan", password="x")

    def local_file(self, bucket, name="doc", content=b"hello", owner=None, **kw):
        vf = VaultFile(owner=owner or self.owner, title=f"{name}.txt", key=name,
                       file_type="text", bucket=bucket, **kw)
        vf.file.save(f"{name}.txt", SimpleUploadedFile(f"{name}.txt", content), save=True)
        return vf

    def s3_bucket(self, name="cloud", sealed=True, provider="aws"):
        adapter = StorageAdapter.get("aws_s3" if provider == "aws" else "ovh_s3")
        bucket = Bucket.objects.create(
            name=name, slug=name, owner=self.owner, storage_backend=StorageBackend.S3,
            provider=adapter.provider(),
            storage_config={"bucket_name": "my-bucket", "region_name": "eu-west-1",
                            "prefix": "vault/"})
        if sealed:
            with override_settings(**PERSISTENT):
                row = BucketSecret(bucket=bucket)
                row.seal({"aws_access_key_id": KEY_ID, "aws_secret_access_key": SECRET})
                row.save()
        return bucket

    def s3_row(self, bucket, key):
        vf = VaultFile(owner=self.owner, title=key, key=key, file_type="text",
                       bucket=bucket, file_size_bytes=3)
        vf.file.name = f"vault/{key}_abcd1234.txt"
        vf.save()
        return vf


# ---------------------------------------------------------------------------
# The model change
# ---------------------------------------------------------------------------

class ModelTests(Fixture):
    def test_the_migration_is_applied(self):
        from django.db import connection
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        self.assertIn(("vault", "0028_bucket_management"), loader.applied_migrations)

    def test_deleting_the_owner_keeps_the_bucket_ownerless(self):
        gone = User.objects.create_user("gone", password="x")
        bucket = Bucket.objects.create(name="Kept", slug="kept", owner=gone, created_by=gone)
        gone.delete()
        bucket.refresh_from_db()
        self.assertIsNone(bucket.owner_id)
        self.assertIsNone(bucket.created_by_id)

    def test_created_at_is_stamped(self):
        bucket = Bucket.objects.create(name="Stamped", slug="stamped", owner=self.owner)
        self.assertIsNotNone(bucket.created_at)

    def test_a_bucket_with_files_cannot_be_deleted_directly(self):
        bucket = Bucket.objects.create(name="Full", slug="full", owner=self.owner)
        self.local_file(bucket)
        with self.assertRaises(ProtectedError):
            bucket.delete()
        self.assertTrue(VaultFile.objects.filter(bucket=bucket).exists())


# ---------------------------------------------------------------------------
# Secret custody
# ---------------------------------------------------------------------------

class FieldKeyTests(TestCase):
    def test_the_random_fallback_is_not_configured(self):
        with override_settings(FIELD_ENCRYPTION_KEY="abc", VAULT_FIELD_KEY_PERSISTENT=False), \
                mock.patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": ""}):
            self.assertFalse(field_key_configured())

    def test_the_environment_key_is_configured(self):
        with override_settings(FIELD_ENCRYPTION_KEY="abc", VAULT_FIELD_KEY_PERSISTENT=False), \
                mock.patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": "abc"}):
            self.assertTrue(field_key_configured())

    def test_an_empty_key_is_never_configured(self):
        with override_settings(FIELD_ENCRYPTION_KEY="", VAULT_FIELD_KEY_PERSISTENT=True):
            self.assertFalse(field_key_configured())


class SealTests(Fixture):
    def test_sealing_refuses_without_a_permanent_key(self):
        bucket = self.s3_bucket(sealed=False)
        row = BucketSecret(bucket=bucket)
        with override_settings(VAULT_FIELD_KEY_PERSISTENT=False), \
                mock.patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": ""}):
            with self.assertRaises(ValidationError) as caught:
                row.seal({"aws_access_key_id": KEY_ID, "aws_secret_access_key": SECRET})
        self.assertIn("FIELD_ENCRYPTION_KEY", caught.exception.messages[0])

    @override_settings(**PERSISTENT)
    def test_sealed_is_ciphertext_with_a_hint(self):
        bucket = self.s3_bucket()
        row = BucketSecret.objects.get(bucket=bucket)
        self.assertNotIn(SECRET.encode(), bytes(row.ciphertext))
        self.assertNotIn(KEY_ID.encode(), bytes(row.ciphertext))
        self.assertEqual(row.hint, KEY_ID[-4:])
        self.assertEqual(row.open(), {"aws_access_key_id": KEY_ID,
                                      "aws_secret_access_key": SECRET})
        self.assertNotIn(SECRET, str(row))

    @override_settings(**PERSISTENT)
    def test_only_the_driver_keys_are_sealed(self):
        bucket = self.s3_bucket(sealed=False)
        row = BucketSecret(bucket=bucket)
        row.seal({"aws_access_key_id": KEY_ID, "aws_secret_access_key": SECRET,
                  "note": "not a credential"})
        self.assertNotIn("note", row.open())

    def test_the_driver_receives_the_opened_dict(self):
        from .storage_backends import S3CompatibleVaultStorageDriver, get_bucket_storage

        bucket = self.s3_bucket()
        boto3, session, client, modules = fake_boto3()
        driver = get_bucket_storage(bucket)
        self.assertIsInstance(driver, S3CompatibleVaultStorageDriver)
        with mock.patch.dict(sys.modules, modules):
            driver._build_client()
        kwargs = boto3.Session.call_args.kwargs
        self.assertEqual(kwargs["aws_access_key_id"], KEY_ID)
        self.assertEqual(kwargs["aws_secret_access_key"], SECRET)

    def test_a_bucket_without_a_secret_keeps_the_environment_chain(self):
        from .storage_backends import get_bucket_storage

        bucket = self.s3_bucket(sealed=False)
        boto3, session, client, modules = fake_boto3()
        with mock.patch.dict(sys.modules, modules):
            get_bucket_storage(bucket)._build_client()
        self.assertEqual(boto3.Session.call_args.kwargs, {})

    def test_an_explicit_credential_wins_over_the_sealed_one(self):
        from .storage_backends import get_bucket_storage

        bucket = self.s3_bucket()
        boto3, session, client, modules = fake_boto3()
        with mock.patch.dict(sys.modules, modules):
            get_bucket_storage(bucket, credential={
                "aws_access_key_id": "PINOPENED1", "aws_secret_access_key": "s"})._build_client()
        self.assertEqual(boto3.Session.call_args.kwargs["aws_access_key_id"], "PINOPENED1")

    def test_a_secret_sealed_under_another_key_refuses_with_a_sentence(self):
        from cryptography.fernet import Fernet

        from .storage_backends import SealedCredentialUnreadable, get_bucket_storage

        bucket = self.s3_bucket()
        with override_settings(FIELD_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            with self.assertRaises(SealedCredentialUnreadable) as caught:
                get_bucket_storage(bucket)
        self.assertIn("FIELD_ENCRYPTION_KEY", str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))

    def test_the_secret_row_is_not_in_the_admin(self):
        with self.assertRaises(NoReverseMatch):
            reverse("admin:vault_bucketsecret_changelist")

    def test_the_bucket_admin_shows_the_hint_and_the_record_read_only(self):
        bucket = self.s3_bucket()
        Bucket.objects.filter(pk=bucket.pk).update(created_by=self.root)
        self.client.force_login(self.root)
        page = self.client.get(reverse("admin:vault_bucket_change", args=[bucket.pk]))
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn(f"…{KEY_ID[-4:]}", html)
        self.assertNotIn(SECRET, html)
        self.assertNotIn(KEY_ID, html)
        self.assertNotIn('name="created_by"', html)
        self.assertNotIn('name="created_at"', html)


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

class RegistryTests(Fixture):
    def test_the_vault_registers_its_kinds(self):
        keys = [a.get_key() for a in StorageAdapter.all()]
        for key in ("local", "aws_s3", "ovh_s3", "zenobia_remote", "s3"):
            self.assertIn(key, keys)
        self.assertEqual([a.get_key() for a in StorageAdapter.creatable_adapters()],
                         ["local", "aws_s3", "ovh_s3", "zenobia_remote"])

    @override_settings(VAULT_EXTERNAL_BUCKETS=False)
    def test_a_local_only_host_offers_local_only(self):
        self.assertEqual([a.get_key() for a in StorageAdapter.creatable_adapters()], ["local"])
        self.assertIsNone(StorageAdapter.for_key("aws_s3"))

    def test_the_generic_s3_kind_is_never_created(self):
        self.assertIsNone(StorageAdapter.for_key("s3"))
        self.assertIsNone(StorageAdapter.for_key("nope"))

    def test_for_bucket_picks_the_kind(self):
        local = Bucket.objects.create(name="L", slug="l", owner=self.owner)
        aws = self.s3_bucket("aws", sealed=False)
        ovh = self.s3_bucket("ovh", sealed=False, provider="ovh")
        other = Bucket.objects.create(name="O", slug="o", owner=self.owner,
                                      storage_backend=StorageBackend.S3,
                                      storage_config={"bucket_name": "x"})
        self.assertEqual(StorageAdapter.for_bucket(local).get_key(), "local")
        self.assertEqual(StorageAdapter.for_bucket(aws).get_key(), "aws_s3")
        self.assertEqual(StorageAdapter.for_bucket(ovh).get_key(), "ovh_s3")
        self.assertEqual(StorageAdapter.for_bucket(other).get_key(), "s3")

    def test_secret_fields_are_flagged(self):
        self.assertEqual(StorageAdapter.get("aws_s3").secret_field_names(), {"secret_access_key"})
        self.assertEqual(StorageAdapter.get("zenobia_remote").secret_field_names(), {"pairing_code"})
        self.assertEqual(StorageAdapter.get("local").secret_field_names(), set())


# ---------------------------------------------------------------------------
# This server
# ---------------------------------------------------------------------------

class LocalAdapterTests(Fixture):
    adapter = property(lambda self: StorageAdapter.get("local"))

    def test_create_records_the_creator_and_the_audit(self):
        config, secret = self.adapter.validate({})
        bucket = self.adapter.create("Team files", self.owner, self.root, config, secret,
                                     storage_quota_mb="500", ai_protected=True)
        self.assertEqual((bucket.owner, bucket.created_by), (self.owner, self.root))
        self.assertEqual(bucket.slug, "team-files")
        self.assertEqual(bucket.storage_backend, "local")
        self.assertEqual(bucket.storage_quota_mb, 500)
        self.assertTrue(bucket.ai_protected)
        self.assertIsNotNone(bucket.created_at)
        self.assertEqual(audit_actions(), ["VAULT.BUCKET.CREATED"])

    def test_create_refuses_a_taken_name_an_inactive_owner_and_no_owner(self):
        Bucket.objects.create(name="Taken", slug="taken", owner=self.owner)
        with self.assertRaises(ValidationError) as caught:
            self.adapter.create("taken", self.owner, self.root, {}, {})
        self.assertIn("name", caught.exception.message_dict)
        idle = User.objects.create_user("idle", password="x", is_active=False)
        with self.assertRaises(ValidationError) as caught:
            self.adapter.create("New", idle, self.root, {}, {})
        self.assertIn("owner", caught.exception.message_dict)
        with self.assertRaises(ValidationError):
            self.adapter.create("New", None, self.root, {}, {})
        self.assertFalse(Bucket.objects.filter(name="New").exists())

    def test_a_name_without_letters_still_gets_a_free_slug(self):
        Bucket.objects.create(name="Other", slug="bucket", owner=self.owner)
        bucket = self.adapter.create("???", self.owner, self.root, {}, {})
        self.assertEqual(bucket.slug, "bucket-2")

    def test_probe_describe_and_destroy_plan(self):
        bucket = self.adapter.create("Probed", self.owner, self.root, {}, {})
        ok, message = self.adapter.probe(bucket)
        self.assertTrue(ok, message)
        bucket.refresh_from_db()
        self.assertIsNotNone(bucket.last_probe_at)
        info = self.adapter.describe(bucket)
        self.assertEqual((info["status"], info["health"]), ("active", "ok"))
        self.assertTrue(info["target"])
        self.local_file(bucket, "a", b"aaaa")
        plan = self.adapter.destroy_plan(bucket)
        self.assertEqual((plan["files"], plan["bytes"]), (1, 4))
        self.assertTrue(plan["removes"])


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------

AWS_FORM = {"bucket_name": "my-bucket", "region": "eu-west-1", "prefix": "",
            "access_key_id": KEY_ID, "secret_access_key": SECRET}


class S3AdapterTests(Fixture):
    aws = property(lambda self: StorageAdapter.get("aws_s3"))
    ovh = property(lambda self: StorageAdapter.get("ovh_s3"))

    def test_validate_splits_config_from_secret(self):
        config, secret = self.aws.validate(AWS_FORM)
        self.assertEqual(config, {"bucket_name": "my-bucket", "region_name": "eu-west-1",
                                  "prefix": "vault/"})
        self.assertEqual(secret, {"aws_access_key_id": KEY_ID, "aws_secret_access_key": SECRET})
        self.assertNotIn(SECRET, json.dumps(config))

    def test_validate_names_each_bad_field(self):
        with self.assertRaises(ValidationError) as caught:
            self.aws.validate({"bucket_name": "Bad_Name", "region": "mars-1",
                               "prefix": "../up", "access_key_id": "", "secret_access_key": ""})
        self.assertEqual(set(caught.exception.message_dict),
                         {"bucket_name", "region", "prefix", "access_key_id", "secret_access_key"})
        self.assertNotIn(SECRET, str(caught.exception))

    def test_a_prefix_keeps_one_trailing_slash(self):
        config, _ = self.aws.validate(dict(AWS_FORM, prefix="/team/files/"))
        self.assertEqual(config["prefix"], "team/files/")

    def test_create_refuses_without_a_permanent_key(self):
        config, secret = self.aws.validate(AWS_FORM)
        with override_settings(VAULT_FIELD_KEY_PERSISTENT=False), \
                mock.patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": ""}):
            with self.assertRaises(ValidationError) as caught:
                self.aws.create("Cloud", self.owner, self.root, config, secret)
        self.assertIn("FIELD_ENCRYPTION_KEY", " ".join(caught.exception.messages))
        self.assertFalse(Bucket.objects.filter(name="Cloud").exists())

    @override_settings(**PERSISTENT)
    def test_create_refuses_when_the_probe_fails_and_saves_nothing(self):
        config, secret = self.aws.validate(AWS_FORM)
        boto3, session, client, modules = fake_boto3(head_error=Exception("403 Forbidden"))
        with mock.patch.dict(sys.modules, modules):
            with self.assertRaises(ValidationError) as caught:
                self.aws.create("Cloud", self.owner, self.root, config, secret)
        self.assertIn("403", " ".join(caught.exception.messages))
        self.assertFalse(Bucket.objects.filter(name="Cloud").exists())
        self.assertFalse(BucketSecret.objects.exists())

    @override_settings(**PERSISTENT)
    def test_create_seals_the_keys_after_a_probe_that_passed(self):
        config, secret = self.aws.validate(AWS_FORM)
        boto3, session, client, modules = fake_boto3()
        with mock.patch.dict(sys.modules, modules):
            ok, message = self.aws.probe_candidate(config, secret)
            self.assertTrue(ok, message)
            bucket = self.aws.create("Cloud", self.owner, self.root, config, secret)
        client.head_bucket.assert_called_with(Bucket="my-bucket")
        self.assertEqual(boto3.Session.call_args.kwargs["aws_secret_access_key"], SECRET)
        self.assertEqual(bucket.provider.name, "aws")
        self.assertEqual(bucket.storage_config, config)
        self.assertEqual(BucketSecret.objects.get(bucket=bucket).open()["aws_secret_access_key"],
                         SECRET)
        self.assertEqual(bucket.created_by, self.root)
        info = self.aws.describe(bucket)
        self.assertIn("my-bucket/vault/", info["target"])
        self.assertIn(KEY_ID[-4:], info["credential"])
        self.assertEqual(info["health"], "ok")
        dumped = json.dumps(info, default=str) + audit_dump()
        self.assertNotIn(SECRET, dumped)
        self.assertNotIn(KEY_ID, dumped)
        self.assertEqual(audit_actions(), ["VAULT.BUCKET.CREATED"])

    def test_probe_is_stamped(self):
        bucket = self.s3_bucket()
        boto3, session, client, modules = fake_boto3(head_error=Exception("NoSuchBucket 404"))
        with mock.patch.dict(sys.modules, modules):
            ok, message = self.aws.probe(bucket)
        self.assertFalse(ok)
        bucket.refresh_from_db()
        self.assertIn("404", bucket.last_probe_error)
        self.assertEqual(self.aws.describe(bucket)["health"], "error")

    @override_settings(**PERSISTENT)
    def test_ovh_region_picks_the_endpoint(self):
        with mock.patch("toto.vault.outbound.socket.getaddrinfo", public_dns):
            config, secret = self.ovh.validate(dict(AWS_FORM, region="gra"))
            boto3, session, client, modules = fake_boto3()
            with mock.patch.dict(sys.modules, modules):
                bucket = self.ovh.create("Ovh", self.owner, self.root, config, secret)
                from .storage_backends import get_bucket_storage

                get_bucket_storage(bucket)._build_client()
        self.assertEqual(session.client.call_args.kwargs["endpoint_url"],
                         "https://s3.gra.io.cloud.ovh.net")
        self.assertEqual(bucket.provider.name, "ovh")
        self.assertNotIn("endpoint_url", bucket.storage_config)

    def test_ovh_refuses_an_unknown_region(self):
        with self.assertRaises(ValidationError) as caught:
            self.ovh.validate(dict(AWS_FORM, region="evil.example.org/x"))
        self.assertIn("region", caught.exception.message_dict)

    def test_an_endpoint_resolving_inward_is_refused(self):
        with mock.patch("toto.vault.outbound.socket.getaddrinfo", private_dns):
            with self.assertRaises(ValidationError) as caught:
                self.ovh.validate(dict(AWS_FORM, region="gra"))
        self.assertIn("not a public address", " ".join(caught.exception.message_dict["region"]))

    def test_the_views_never_need_the_provider(self):
        # Every kind answers the same four questions; nothing is provider-shaped.
        bucket = self.s3_bucket()
        for key in ("fields", "describe", "destroy_plan"):
            self.assertTrue(hasattr(self.aws, key))
        self.assertEqual(set(self.aws.describe(bucket)) - {"detail", "credential"},
                         {"target", "status", "status_label", "health", "health_label"})


# ---------------------------------------------------------------------------
# Another Zenobia
# ---------------------------------------------------------------------------

@override_settings(**PERSISTENT)
class RemoteAdapterTests(Fixture):
    adapter = property(lambda self: StorageAdapter.get("zenobia_remote"))

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.exporter = User.objects.create_user("exporter", password="x")
        cls.source = Bucket.objects.create(name="source", slug="source", owner=cls.exporter)

    def code(self, *, expires_at="default", host="https://peer.example.org", **rights):
        grant = BucketGrant.objects.create(label="loop", bucket=self.source,
                                           **(rights or {"may_list": True}))
        if expires_at != "default":
            grant.expires_at = expires_at
        raw = grant.issue_api_key()
        grant.save()
        return pairing_code_for(grant, raw, host=host), grant, raw

    def test_decode_preview_shows_what_the_code_grants_without_saving(self):
        code, grant, raw = self.code(may_list=True, may_download=True)
        before = BucketPeer.objects.count()
        preview = self.adapter.decode_preview(code)
        self.assertEqual(preview["host"], "https://peer.example.org")
        self.assertEqual(preview["bucket"], "source")
        self.assertEqual(preview["rights"], ["may_list", "may_download"])
        self.assertFalse(preview["expired"])
        self.assertIsNotNone(preview["expires_at"])
        self.assertEqual(preview["already_connected"], "")
        self.assertEqual(BucketPeer.objects.count(), before)
        dumped = json.dumps(preview, default=str)
        for secret in (raw, grant.magic_token, str(grant.grant_uid)):
            self.assertNotIn(secret, dumped)

    def test_an_old_code_without_host_or_expiry_still_previews(self):
        code, grant, raw = self.code(host="")
        payload = json.loads(base64.b64decode(code))
        payload.pop("expires_at")
        old = base64.b64encode(json.dumps(payload).encode()).decode()
        preview = self.adapter.decode_preview(old)
        self.assertEqual((preview["host"], preview["expires_at"]), ("", None))

    def test_malformed_expired_and_connected_codes_are_refused(self):
        with self.assertRaises(ValidationError) as caught:
            self.adapter.validate({"pairing_code": "not-a-code"})
        self.assertIn("pairing_code", caught.exception.message_dict)
        expired, _, _ = self.code(expires_at=timezone.now() - timedelta(minutes=1))
        with self.assertRaises(ValidationError) as caught:
            self.adapter.validate({"pairing_code": expired})
        self.assertIn("expired", " ".join(caught.exception.message_dict["pairing_code"]))
        code, grant, raw = self.code()
        BucketPeer.objects.create(label="Already", base_url="https://peer.example.org",
                                  grant_uid=grant.grant_uid, magic_token=grant.magic_token)
        with self.assertRaises(ValidationError) as caught:
            self.adapter.validate({"pairing_code": code})
        self.assertIn("Already", " ".join(caught.exception.message_dict["pairing_code"]))
        self.assertTrue(self.adapter.decode_preview(code)["already_connected"])

    def test_the_address_is_ssrf_guarded(self):
        code, _, _ = self.code(host="")
        with self.assertRaises(ValidationError) as caught:
            self.adapter.validate({"pairing_code": code})
        self.assertIn("base_url", caught.exception.message_dict)
        with self.assertRaises(ValidationError) as caught:
            self.adapter.validate({"pairing_code": code, "base_url": "https://169.254.169.254"})
        self.assertIn("base_url", caught.exception.message_dict)

    def test_validate_keeps_the_secrets_out_of_the_config(self):
        code, grant, raw = self.code()
        config, secret = self.adapter.validate({"pairing_code": code})
        self.assertEqual(config["base_url"], "https://peer.example.org")
        self.assertEqual(config["remote_bucket"], "source")
        dumped = json.dumps(config)
        for value in (raw, grant.magic_token, str(grant.grant_uid)):
            self.assertNotIn(value, dumped)
        self.assertEqual(secret["api_key"], raw)

    def test_probe_candidate_and_create_through_the_loopback(self):
        code, grant, raw = self.code()
        config, secret = self.adapter.validate({"pairing_code": code})
        with mock.patch("toto.vault.peer_client._http", return_value=LoopbackHttp()):
            ok, message = self.adapter.probe_candidate(config, secret)
            self.assertTrue(ok, message)
            self.assertFalse(BucketPeer.objects.exists())
            bucket = self.adapter.create("Mounted", self.owner, self.root, config, secret)
        peer = bucket.peer
        self.assertEqual(bucket.storage_backend, "remote_toto")
        self.assertEqual(bucket.created_by, self.root)
        self.assertEqual(peer.paired_by, self.root)
        self.assertEqual(peer.get_api_key(), raw)
        self.assertNotIn(raw.encode(), bytes(peer.api_key_encrypted))
        peer.refresh_from_db()
        self.assertIsNotNone(peer.last_ok_at)
        self.assertEqual(self.adapter.describe(bucket)["health"], "ok")
        self.assertNotIn(grant.magic_token, audit_dump())
        self.assertNotIn(raw, audit_dump())

    def test_a_failed_first_probe_keeps_the_mount_and_hides_the_token(self):
        code, grant, raw = self.code()
        config, secret = self.adapter.validate({"pairing_code": code})

        class Leaky:
            def request(self, method, url, **kwargs):
                raise ConnectionError(f"Max retries exceeded with url: {url}")

        with mock.patch("toto.vault.peer_client._http", return_value=Leaky()):
            ok, message = self.adapter.probe_candidate(config, secret)
            self.assertFalse(ok)
            self.assertNotIn(grant.magic_token, message)
            bucket = self.adapter.create("Mounted", self.owner, self.root, config, secret)
        peer = BucketPeer.objects.get(pk=bucket.peer_id)
        self.assertTrue(peer.last_error)
        self.assertNotIn(grant.magic_token, peer.last_error)
        self.assertNotIn(grant.magic_token, peer.probe_error)
        self.assertEqual(self.adapter.describe(bucket)["health"], "error")

    def test_create_asks_again_when_minutes_pass_between_steps(self):
        code, grant, raw = self.code()
        config, secret = self.adapter.validate({"pairing_code": code})
        stale = dict(config, expires_at=(timezone.now() - timedelta(seconds=1)).isoformat())
        with self.assertRaises(ValidationError):
            self.adapter.create("Late", self.owner, self.root, stale, secret)
        BucketPeer.objects.create(label="Meanwhile", base_url="https://peer.example.org",
                                  grant_uid=grant.grant_uid, magic_token=grant.magic_token)
        with self.assertRaises(ValidationError) as caught:
            self.adapter.create("Twice", self.owner, self.root, config, secret)
        self.assertIn("Meanwhile", " ".join(caught.exception.messages))
        self.assertFalse(Bucket.objects.filter(name__in=["Late", "Twice"]).exists())

    def test_create_refuses_without_a_permanent_key(self):
        code, grant, raw = self.code()
        config, secret = self.adapter.validate({"pairing_code": code})
        with override_settings(VAULT_FIELD_KEY_PERSISTENT=False), \
                mock.patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": ""}):
            with self.assertRaises(ValidationError):
                self.adapter.create("Mounted", self.owner, self.root, config, secret)
        self.assertFalse(BucketPeer.objects.exists())


# ---------------------------------------------------------------------------
# Ownerless buckets
# ---------------------------------------------------------------------------

class OwnerlessTests(Fixture):
    def setUp(self):
        self.orphan = Bucket.objects.create(name="Orphan", slug="orphan", owner=None)
        self.file = self.local_file(self.orphan, "private")

    def test_nobody_reads_through_the_bucket_owner_clause(self):
        from django.contrib.auth.models import AnonymousUser

        from .access import may_read
        from .filetree import accessible_files

        self.assertFalse(may_read(AnonymousUser(), self.file))
        self.assertFalse(may_read(self.stranger, self.file))
        self.assertTrue(may_read(self.owner, self.file))      # the FILE's owner
        self.assertNotIn(self.file, accessible_files(self.stranger))

    def test_the_bucket_page_and_its_clearance_door_stay_404(self):
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(reverse("vault:bucket_metrics", args=["orphan"]))
                         .status_code, 404)
        self.assertEqual(self.client.post(reverse("vault:bucket_clearances", args=["orphan"]))
                         .status_code, 404)
        self.client.force_login(self.root)
        from toto.core.models import Platform

        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        self.assertEqual(self.client.get(reverse("vault:bucket_metrics", args=["orphan"]))
                         .status_code, 200)

    def test_the_clearance_plugin_describes_it(self):
        from toto.socialhub.plugins.clearance_plugins import ClearanceTargetPlugin

        plugin = ClearanceTargetPlugin.get("vault.bucket")
        self.assertEqual(plugin.detail(self.orphan), "")

    def test_a_personal_bucket_that_lost_its_owner_is_never_handed_on(self):
        Bucket.objects.create(name="Personal — newbie", slug="personal-newbie", owner=None)
        newbie = User.objects.create_user("newbie", password="x")
        mine = personal_bucket(newbie)
        self.assertEqual(mine.slug, "personal-newbie-2")
        self.assertEqual(mine.owner, newbie)
        self.assertEqual(personal_bucket(newbie), mine)

    def test_the_api_default_bucket_uses_the_same_rule(self):
        from .views import resolve_new_file_target

        Bucket.objects.create(name="Personal — stan", slug="personal-stan", owner=None)
        bucket, directory = resolve_new_file_target(self.stranger)
        self.assertEqual((bucket.slug, bucket.owner), ("personal-stan-2", self.stranger))

    def test_a_mirror_refresh_of_an_ownerless_mount_refuses(self):
        peer = BucketPeer.objects.create(label="P", base_url="https://peer.example.org",
                                         grant_uid="9b0f3f0e-5f5c-4c7c-9d4e-9b3a3a0c7a11",
                                         magic_token="tok")
        mount = Bucket.objects.create(name="Mount", slug="mount", owner=None,
                                      storage_backend=StorageBackend.REMOTE_TOTO, peer=peer)
        run = mirror.BucketRefreshRun.objects.create(bucket=mount)
        http = NoHttp()
        with mock.patch("toto.vault.peer_client._http", return_value=http):
            run = mirror.execute_refresh_run(run.pk)
        self.assertEqual(run.status, mirror.RefreshStatus.FAILED)
        self.assertIn("no owner", run.error)
        self.assertEqual(http.calls, [])

    def test_a_peer_upload_into_an_ownerless_export_is_refused(self):
        grant = BucketGrant.objects.create(label="p", bucket=self.orphan, may_upload=True)
        raw = grant.issue_api_key()
        grant.save()
        url = reverse("vault:peer_files", kwargs={"grant_uid": grant.grant_uid,
                                                  "magic_token": grant.magic_token})
        response = self.client.post(url, {"file": SimpleUploadedFile("x.txt", b"x")},
                                    HTTP_X_VAULT_API_KEY=raw)
        self.assertEqual(response.status_code, 409)
        self.assertIn("no owner", response.json()["error"])
        self.assertEqual(VaultFile.objects.filter(bucket=self.orphan).count(), 1)

    def test_creating_an_empty_file_in_an_ownerless_bucket_is_refused(self):
        from .plugins import VaultEditorPlugin
        from .views import available_create_types

        types = [t for t, _label in available_create_types() if VaultEditorPlugin.for_file_type(t)]
        if not types:
            self.skipTest("no editor is mounted on this host")
        directory = VaultDirectory.objects.create(name="d", bucket=self.orphan, owner=self.owner)
        self.client.force_login(self.owner)
        response = self.client.post(reverse("vault:create_file"),
                                    {"directory_id": directory.pk, "title": "new-one",
                                     "file_type": types[0]})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(VaultFile.objects.filter(title__startswith="new-one").exists())

    def test_edit_gives_it_an_owner_and_records_it(self):
        changed = bucket_lifecycle.update_bucket(self.orphan, self.root, owner=self.owner)
        self.assertEqual(changed, ["owner"])
        self.orphan.refresh_from_db()
        self.assertEqual(self.orphan.owner, self.owner)
        from toto.audit.models import AuditRecord

        record = AuditRecord.objects.get(action="VAULT.BUCKET.UPDATED")
        self.assertEqual(record.metadata["changes"]["owner"], {"before": None, "after": "olga"})

    def test_describe_and_snapshot_cope(self):
        info = StorageAdapter.for_bucket(self.orphan).describe(self.orphan)
        self.assertEqual(info["status"], "active")
        self.assertIsNone(bucket_lifecycle.snapshot(self.orphan)["owner"])


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

class UpdateTests(Fixture):
    def setUp(self):
        self.bucket = Bucket.objects.create(name="Edit me", slug="edit-me", owner=self.owner)

    def test_the_four_editable_fields_and_their_record(self):
        changed = bucket_lifecycle.update_bucket(
            self.bucket, self.root, name="Edited", storage_quota_mb="10", ai_protected=True,
            owner=self.stranger)
        self.assertEqual(sorted(changed), ["ai_protected", "name", "owner", "storage_quota_mb"])
        self.bucket.refresh_from_db()
        self.assertEqual((self.bucket.name, self.bucket.slug, self.bucket.storage_quota_mb),
                         ("Edited", "edit-me", 10))
        from toto.audit.models import AuditRecord

        changes = AuditRecord.objects.get(action="VAULT.BUCKET.UPDATED").metadata["changes"]
        self.assertEqual(changes["name"], {"before": "Edit me", "after": "Edited"})

    def test_nothing_that_moves_data_can_be_edited(self):
        for field in ("storage_backend", "provider", "storage_config", "peer", "slug",
                      "created_by"):
            with self.assertRaises(ValidationError):
                bucket_lifecycle.update_bucket(self.bucket, self.root, **{field: "x"})

    def test_no_change_records_nothing(self):
        self.assertEqual(bucket_lifecycle.update_bucket(self.bucket, self.root, name="Edit me"), [])
        self.assertEqual(audit_actions(), [])

    def test_a_bucket_being_deleted_refuses_edit(self):
        Bucket.objects.filter(pk=self.bucket.pk).update(deletion_requested_at=timezone.now())
        with self.assertRaises(ValidationError):
            bucket_lifecycle.update_bucket(self.bucket, self.root, name="Late")


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@override_settings(VAULT_PURGE_INLINE=True)
class DeletionTests(Fixture):
    def delete(self, bucket, name=None, actor=None):
        with self.captureOnCommitCallbacks(execute=True):
            bucket_lifecycle.request_deletion(bucket, actor or self.root,
                                              confirm_name=bucket.name if name is None else name)

    def test_the_typed_name_must_match(self):
        bucket = Bucket.objects.create(name="Precious", slug="precious", owner=self.owner)
        with self.assertRaises(ValidationError) as caught:
            self.delete(bucket, name="precious")
        self.assertIn("confirm_name", caught.exception.message_dict)
        bucket.refresh_from_db()
        self.assertFalse(bucket.is_being_deleted)

    def test_a_local_bucket_goes_with_its_rows_bytes_and_folders(self):
        from .models import FileGateway

        bucket = Bucket.objects.create(name="Local", slug="local-b", owner=self.owner)
        folder = VaultDirectory.objects.create(name="f", bucket=bucket, owner=self.owner)
        FileGateway.objects.create(name="g", directory=folder, bucket=bucket)
        files = [self.local_file(bucket, "a", directory=folder), self.local_file(bucket, "b")]
        paths = [f.file.path for f in files]
        self.assertTrue(all(os.path.exists(p) for p in paths))
        loose = VaultFile.objects.filter(bucket__isnull=True).count()
        self.delete(bucket)
        self.assertFalse(Bucket.objects.filter(pk=bucket.pk).exists())
        self.assertFalse(VaultFile.objects.filter(pk__in=[f.pk for f in files]).exists())
        self.assertFalse(any(os.path.exists(p) for p in paths))
        self.assertFalse(VaultDirectory.objects.filter(pk=folder.pk).exists())
        self.assertEqual(VaultFile.objects.filter(bucket__isnull=True).count(), loose)
        self.assertEqual(audit_actions(), ["VAULT.BUCKET.DELETE_REQUESTED", "VAULT.BUCKET.DELETED"])
        from toto.audit.models import AuditRecord

        done = AuditRecord.objects.get(action="VAULT.BUCKET.DELETED")
        self.assertEqual(done.actor_user, self.root)
        self.assertEqual(done.metadata["files_deleted"], 2)

    def test_an_s3_bucket_deletes_every_object_through_the_driver(self):
        bucket = self.s3_bucket()
        rows = [self.s3_row(bucket, "one"), self.s3_row(bucket, "two")]
        names = sorted(r.file.name for r in rows)
        boto3, session, client, modules = fake_boto3()
        with mock.patch.dict(sys.modules, modules):
            self.delete(bucket)
        self.assertFalse(Bucket.objects.filter(pk=bucket.pk).exists())
        self.assertFalse(BucketSecret.objects.filter(bucket_id=bucket.pk).exists())
        deleted = sorted(c.kwargs["Key"] for c in client.delete_object.call_args_list)
        self.assertEqual(deleted, names)
        self.assertTrue(all(c.kwargs["Bucket"] == "my-bucket"
                            for c in client.delete_object.call_args_list))
        # The sealed key opened the driver, once, for the job.
        self.assertEqual(boto3.Session.call_args.kwargs["aws_secret_access_key"], SECRET)

    def test_a_mount_deletes_rows_only_and_never_contacts_the_other_host(self):
        peer = BucketPeer.objects.create(label="P", base_url="https://peer.example.org",
                                         grant_uid="9b0f3f0e-5f5c-4c7c-9d4e-9b3a3a0c7a11",
                                         magic_token="tok")
        mount = Bucket.objects.create(name="Mount", slug="mount", owner=self.owner,
                                      storage_backend=StorageBackend.REMOTE_TOTO, peer=peer)
        for key in ("r1", "r2"):
            stub = VaultFile(owner=self.owner, title=key, key=key, file_type="text",
                             bucket=mount, origin=FileOrigin.MIRROR, file_size_bytes=1)
            stub.file.name = key
            stub.save()
        http = NoHttp()
        with mock.patch("toto.vault.peer_client._http", return_value=http):
            self.delete(mount)
        self.assertEqual(http.calls, [])
        self.assertFalse(Bucket.objects.filter(pk=mount.pk).exists())
        self.assertFalse(VaultFile.objects.filter(key__in=["r1", "r2"]).exists())
        self.assertFalse(BucketPeer.objects.filter(pk=peer.pk).exists())

    def test_a_pairing_another_bucket_uses_is_kept(self):
        peer = BucketPeer.objects.create(label="P", base_url="https://peer.example.org",
                                         grant_uid="9b0f3f0e-5f5c-4c7c-9d4e-9b3a3a0c7a11",
                                         magic_token="tok")
        first = Bucket.objects.create(name="M1", slug="m1", owner=self.owner,
                                      storage_backend=StorageBackend.REMOTE_TOTO, peer=peer)
        Bucket.objects.create(name="M2", slug="m2", owner=self.owner,
                              storage_backend=StorageBackend.REMOTE_TOTO, peer=peer)
        with mock.patch("toto.vault.peer_client._http", return_value=NoHttp()):
            self.delete(first)
        self.assertTrue(BucketPeer.objects.filter(pk=peer.pk).exists())

    def test_a_held_file_stops_the_purge_and_the_keeping_stays(self):
        from toto.socialhub.models import Clearance

        bucket = Bucket.objects.create(name="Kept", slug="kept", owner=self.owner)
        clearance = Clearance.objects.create(name="restricted", slug="restricted")
        BucketClearance.objects.create(bucket=bucket, clearance=clearance)
        held = self.local_file(bucket, "held")
        free = self.local_file(bucket, "free")
        from . import purge

        real = purge.purge_file

        def pinned(vault_file, **kwargs):
            if vault_file.pk == held.pk:
                raise ProtectedError("pinned", [vault_file])
            return real(vault_file, **kwargs)

        with mock.patch("toto.vault.purge.purge_file", pinned):
            self.delete(bucket)
        bucket.refresh_from_db()
        self.assertTrue(bucket.is_being_deleted)
        self.assertIn("held", bucket.deletion_error)
        self.assertFalse(VaultFile.objects.filter(pk=free.pk).exists())
        held.refresh_from_db()
        self.assertEqual(held.bucket_id, bucket.pk)           # never bucket=None
        self.assertTrue(BucketClearance.objects.filter(bucket=bucket).exists())
        self.assertEqual(StorageAdapter.for_bucket(bucket).describe(bucket)["status"],
                         "delete_failed")
        self.assertIn("VAULT.BUCKET.DELETE_FAILED", audit_actions())
        # Confirming again once the file is free finishes the job.
        self.delete(bucket)
        self.assertFalse(Bucket.objects.filter(pk=bucket.pk).exists())

    def test_nothing_new_lands_in_a_bucket_being_deleted(self):
        from .storage_backends import UploadRefused, persist_upload

        bucket = Bucket.objects.create(name="Closing", slug="closing", owner=self.owner)
        existing = self.local_file(bucket, "old")
        with override_settings(VAULT_PURGE_INLINE=False), \
                mock.patch("toto.celery_utils.celery_available", return_value=False):
            with self.captureOnCommitCallbacks(execute=True):
                bucket_lifecycle.request_deletion(bucket, self.root, confirm_name="Closing")
        bucket.refresh_from_db()
        self.assertTrue(bucket.is_being_deleted)
        self.assertIn("worker", bucket.deletion_error)       # no worker: said, not hidden
        with self.assertRaises(UploadRefused):
            persist_upload(VaultFile(owner=self.owner, title="n.txt", file_type="text",
                                     bucket=bucket), SimpleUploadedFile("n.txt", b"n"))
        with self.assertRaises(BucketClosed):
            VaultFile.objects.create(owner=self.owner, title="m", key="m", file_type="text",
                                     bucket=bucket, file=SimpleUploadedFile("m.txt", b"m"))
        other = Bucket.objects.create(name="Other", slug="other", owner=self.owner)
        moved = self.local_file(other, "mv")
        moved.bucket = bucket
        with self.assertRaises(BucketClosed):
            moved.save()
        existing.notes = "a stamp on a file already there is fine"
        existing.save()
        # The owner's personal bucket being deleted is skipped, not reused.
        Bucket.objects.filter(pk=bucket.pk).update(slug="personal-olga", name="Personal — olga")
        self.assertEqual(personal_bucket(self.owner).slug, "personal-olga-2")

    def test_the_purge_needs_the_mark_and_is_idempotent(self):
        bucket = Bucket.objects.create(name="Unmarked", slug="unmarked", owner=self.owner)
        self.assertFalse(bucket_lifecycle.purge_bucket(bucket.pk)["ok"])
        self.assertTrue(Bucket.objects.filter(pk=bucket.pk).exists())
        self.assertTrue(bucket_lifecycle.purge_bucket(999999)["gone"])

    def test_the_destroy_plans_say_what_stays(self):
        peer = BucketPeer.objects.create(label="P", base_url="https://peer.example.org",
                                         grant_uid="9b0f3f0e-5f5c-4c7c-9d4e-9b3a3a0c7a11",
                                         magic_token="tok")
        mount = Bucket.objects.create(name="Mount", slug="mount", owner=self.owner,
                                      storage_backend=StorageBackend.REMOTE_TOTO, peer=peer)
        plan = StorageAdapter.for_bucket(mount).destroy_plan(mount)
        self.assertIn("nothing is deleted there", " ".join(plan["keeps"]))
        s3 = self.s3_bucket()
        self.assertTrue(StorageAdapter.for_bucket(s3).destroy_plan(s3)["keeps"])


# ---------------------------------------------------------------------------
# Regressions (review of 2026-09-30)
# ---------------------------------------------------------------------------

class MigrationSqlTests(SimpleTestCase):
    """0028 must not stamp the deploy's own time on every older bucket."""

    databases = {"default"}

    def test_older_buckets_keep_no_creation_date(self):
        import io
        import re

        from django.core.management import call_command

        out = io.StringIO()
        call_command("sqlmigrate", "vault", "0028", stdout=out)
        sql = out.getvalue()
        self.assertIn("created_at", sql)
        # An AddField(auto_now_add=True) copies a literal now() into every
        # existing row (SQLite's table rebuild, or Postgres' ADD ... DEFAULT).
        self.assertIsNone(re.search(r"'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", sql), sql)


class LegacyEditTests(Fixture):
    """Edit checks a field only when its value changes: what a bucket
    already has never blocks changing something else."""

    def test_a_legacy_twin_name_differing_in_case(self):
        Bucket.objects.create(name="Finance", slug="finance-1", owner=self.owner)
        twin = Bucket.objects.create(name="finance", slug="finance-2", owner=self.owner)
        changed = bucket_lifecycle.update_bucket(twin, self.root, name="finance",
                                                 owner=self.owner, storage_quota_mb="",
                                                 ai_protected=True)
        self.assertEqual(changed, ["ai_protected"])
        with self.assertRaises(ValidationError) as caught:     # a NEW clash is still refused
            bucket_lifecycle.update_bucket(twin, self.root, name="FINANCE")
        self.assertIn("name", caught.exception.message_dict)

    def test_an_owner_deactivated_since(self):
        bucket = Bucket.objects.create(name="Kept", slug="kept-l", owner=self.stranger)
        User.objects.filter(pk=self.stranger.pk).update(is_active=False)
        gone = User.objects.get(pk=self.stranger.pk)
        changed = bucket_lifecycle.update_bucket(bucket, self.root, name="Kept", owner=gone,
                                                 storage_quota_mb="50", ai_protected=False)
        self.assertEqual(changed, ["storage_quota_mb"])
        bucket.refresh_from_db()
        self.assertEqual((bucket.owner_id, bucket.storage_quota_mb), (gone.pk, 50))
        other = Bucket.objects.create(name="Other", slug="other-l", owner=self.owner)
        with self.assertRaises(ValidationError) as caught:     # handing it TO them is refused
            bucket_lifecycle.update_bucket(other, self.root, owner=gone)
        self.assertIn("owner", caught.exception.message_dict)

    def test_a_quota_of_zero_from_the_admin(self):
        bucket = Bucket.objects.create(name="Zero", slug="zero-l", owner=self.owner,
                                       storage_quota_mb=0)
        changed = bucket_lifecycle.update_bucket(bucket, self.root, storage_quota_mb="0",
                                                 ai_protected=True)
        self.assertEqual(changed, ["ai_protected"])
        roomy = Bucket.objects.create(name="Roomy", slug="roomy-l", owner=self.owner,
                                      storage_quota_mb=5)
        with self.assertRaises(ValidationError):              # setting 0 anew is still refused
            bucket_lifecycle.update_bucket(roomy, self.root, storage_quota_mb="0")

    def test_an_ownerless_bucket_can_be_edited_without_choosing_an_owner(self):
        orphan = Bucket.objects.create(name="Orphaned", slug="orphaned-l", owner=None)
        changed = bucket_lifecycle.update_bucket(orphan, self.root, name="Orphaned", owner=None,
                                                 storage_quota_mb="3", ai_protected=False)
        self.assertEqual(changed, ["storage_quota_mb"])


@override_settings(VAULT_PURGE_INLINE=True)
class PurgeHardeningTests(Fixture):
    def delete(self, bucket):
        with self.captureOnCommitCallbacks(execute=True):
            bucket_lifecycle.request_deletion(bucket, self.root, confirm_name=bucket.name)

    def mark(self, bucket):
        from .models import Bucket as B

        B.objects.filter(pk=bucket.pk).update(deletion_requested_at=timezone.now())
        bucket.refresh_from_db()

    # -- bytes that will not go ---------------------------------------------

    def test_objects_the_provider_refuses_to_delete_keep_the_bucket_its_key_and_its_rows(self):
        bucket = self.s3_bucket()
        rows = [self.s3_row(bucket, "one"), self.s3_row(bucket, "two")]
        boto3, session, client, modules = fake_boto3()
        client.delete_object.side_effect = Exception(
            "An error occurred (AccessDenied) when calling the DeleteObject operation")
        with mock.patch.dict(sys.modules, modules):
            self.delete(bucket)
        bucket.refresh_from_db()
        self.assertTrue(bucket.is_being_deleted)
        self.assertIn("AccessDenied", bucket.deletion_error)
        self.assertEqual(VaultFile.objects.filter(pk__in=[r.pk for r in rows]).count(), 2)
        self.assertTrue(BucketSecret.objects.filter(bucket_id=bucket.pk).exists())
        self.assertEqual(StorageAdapter.for_bucket(bucket).describe(bucket)["status"],
                         "delete_failed")
        actions = audit_actions()
        self.assertIn("VAULT.BUCKET.DELETE_FAILED", actions)
        self.assertNotIn("VAULT.BUCKET.DELETED", actions)
        self.assertNotIn(SECRET, audit_dump())
        # The key fixed at the provider: confirming again finishes the job.
        boto3, session, client, modules = fake_boto3()
        with mock.patch.dict(sys.modules, modules):
            self.delete(bucket)
        self.assertFalse(Bucket.objects.filter(pk=bucket.pk).exists())
        self.assertEqual(client.delete_object.call_count, 2)

    def test_a_run_of_refusals_stops_early(self):
        bucket = self.s3_bucket()
        for n in range(bucket_lifecycle.PURGE_MAX_FAILURES + 5):
            self.s3_row(bucket, f"k{n}")
        boto3, session, client, modules = fake_boto3()
        client.delete_object.side_effect = Exception("AccessDenied")
        with mock.patch.dict(sys.modules, modules):
            self.delete(bucket)
        self.assertEqual(client.delete_object.call_count, bucket_lifecycle.PURGE_MAX_FAILURES)
        bucket.refresh_from_db()
        self.assertIn("AccessDenied", bucket.deletion_error)

    def test_bytes_this_server_cannot_unlink_keep_their_row(self):
        bucket = Bucket.objects.create(name="Sticky", slug="sticky", owner=self.owner)
        row = self.local_file(bucket, "sticky")
        path = row.file.path
        real_remove = os.remove

        def refuse(target, *args, **kwargs):
            if os.path.abspath(str(target)) == os.path.abspath(path):
                raise PermissionError(13, "Permission denied", path)
            return real_remove(target, *args, **kwargs)

        with mock.patch("os.remove", refuse):
            self.delete(bucket)
        bucket.refresh_from_db()
        self.assertTrue(os.path.exists(path))
        self.assertTrue(VaultFile.objects.filter(pk=row.pk).exists())
        self.assertIn("Permission denied", bucket.deletion_error)
        self.assertNotIn("VAULT.BUCKET.DELETED", audit_actions())

    # -- version bodies -------------------------------------------------------

    def test_the_bodies_of_saved_versions_go_with_the_bucket(self):
        from django.core.files.storage import default_storage

        from . import versions
        from .models import VersionBlob

        bucket = Bucket.objects.create(name="Drafts", slug="drafts", owner=self.owner)
        doc = self.local_file(bucket, "doc", b"first")
        versions.save_version(doc, body=b"first draft", author=self.owner)
        versions.save_version(doc, body=b"second draft", author=self.owner)
        # A body another file (elsewhere) also cites stays for that file.
        elsewhere = Bucket.objects.create(name="Elsewhere", slug="elsewhere", owner=self.owner)
        other = self.local_file(elsewhere, "other", b"x")
        versions.save_version(other, body=b"second draft", author=self.owner)
        blobs = {b.content_hash: b.data.name for b in VersionBlob.objects.all()}
        self.assertEqual(len(blobs), 2)
        self.assertTrue(all(default_storage.exists(name) for name in blobs.values()))

        self.delete(bucket)

        self.assertFalse(Bucket.objects.filter(pk=bucket.pk).exists())
        left = list(VersionBlob.objects.values_list("data", flat=True))
        self.assertEqual(len(left), 1)
        self.assertEqual(VersionBlob.objects.get().versions.get().file_id, other.pk)
        gone = [name for name in blobs.values() if name not in left]
        self.assertEqual(len(gone), 1)
        self.assertFalse(default_storage.exists(gone[0]))
        self.assertTrue(default_storage.exists(left[0]))

    # -- a job never dies silently -------------------------------------------

    def test_the_soft_time_limit_is_never_swallowed_as_one_failed_file(self):
        from celery.exceptions import SoftTimeLimitExceeded

        from .tasks import purge_bucket_task

        bucket = Bucket.objects.create(name="Slow", slug="slow", owner=self.owner)
        first, second = self.local_file(bucket, "a"), self.local_file(bucket, "b")
        self.mark(bucket)
        calls = []

        def slow(vault_file, **kwargs):
            calls.append(vault_file.pk)
            raise SoftTimeLimitExceeded()

        with mock.patch("toto.vault.purge.purge_file", slow):
            with self.assertRaises(SoftTimeLimitExceeded):
                bucket_lifecycle.purge_bucket(bucket.pk, actor_pk=self.root.pk)
            self.assertEqual(calls, [first.pk])                  # not carried on to the next
            result = purge_bucket_task(bucket.pk, self.root.pk)
        self.assertFalse(result["ok"])
        bucket.refresh_from_db()
        self.assertIn("ran out of time", bucket.deletion_error)
        self.assertEqual(StorageAdapter.for_bucket(bucket).describe(bucket)["status"], "delete_failed")
        self.assertIn("VAULT.BUCKET.DELETE_FAILED", audit_actions())
        self.assertEqual(VaultFile.objects.filter(pk__in=[first.pk, second.pk]).count(), 2)

    def test_the_soft_time_limit_inside_a_driver_delete_is_not_swallowed_either(self):
        from celery.exceptions import SoftTimeLimitExceeded

        from .purge import _delete_blob
        from .storage_backends import S3CompatibleVaultStorageDriver

        driver = S3CompatibleVaultStorageDriver({"bucket_name": "b"})
        client = MagicMock()
        client.delete_object.side_effect = SoftTimeLimitExceeded()
        driver._client = client
        with self.assertRaises(SoftTimeLimitExceeded):
            driver.delete("k")
        with self.assertRaises(SoftTimeLimitExceeded):
            _delete_blob(None, "k", driver)

    def test_an_error_escaping_the_purge_is_stamped_and_audited(self):
        from .tasks import purge_bucket_task

        bucket = Bucket.objects.create(name="Broken", slug="broken", owner=self.owner)
        self.mark(bucket)
        with mock.patch("toto.vault.bucket_lifecycle.purge_bucket",
                        side_effect=RuntimeError("the database went away")):
            result = purge_bucket_task(bucket.pk, self.root.pk)
        self.assertFalse(result["ok"])
        bucket.refresh_from_db()
        self.assertIn("the database went away", bucket.deletion_error)
        self.assertEqual(StorageAdapter.for_bucket(bucket).describe(bucket)["status"], "delete_failed")
        from toto.audit.models import AuditRecord

        record = AuditRecord.objects.get(action="VAULT.BUCKET.DELETE_FAILED")
        self.assertEqual(record.actor_user, self.root)

    def test_a_run_stops_at_its_budget_and_queues_the_next(self):
        from .tasks import purge_bucket_task

        bucket = Bucket.objects.create(name="Big", slug="big", owner=self.owner)
        files = [self.local_file(bucket, f"f{n}") for n in range(3)]
        self.mark(bucket)
        # A budget of nothing: every run still handles one file, then hands on.
        with mock.patch.object(bucket_lifecycle, "PURGE_BUDGET_SECONDS", 0), \
                mock.patch.object(bucket_lifecycle, "queue_purge", return_value=True) as queued:
            with self.captureOnCommitCallbacks(execute=True):
                result = purge_bucket_task(bucket.pk, self.root.pk)
        self.assertEqual(result, {"ok": False, "more": True, "files_deleted": 1})
        queued.assert_called_once_with(bucket.pk, actor_pk=self.root.pk)
        self.assertEqual(VaultFile.objects.filter(pk__in=[f.pk for f in files]).count(), 2)
        bucket.refresh_from_db()
        self.assertEqual(bucket.deletion_error, "")               # paused, not failed
        # The next runs finish it.
        with mock.patch.object(bucket_lifecycle, "PURGE_BUDGET_SECONDS", 0), \
                mock.patch.object(bucket_lifecycle, "queue_purge", return_value=True):
            for _ in range(3):
                with self.captureOnCommitCallbacks(execute=True):
                    result = purge_bucket_task(bucket.pk, self.root.pk)
        self.assertTrue(result["ok"], result)
        self.assertFalse(Bucket.objects.filter(pk=bucket.pk).exists())

    def test_a_paused_run_with_no_worker_for_the_rest_says_so(self):
        from .tasks import purge_bucket_task

        bucket = Bucket.objects.create(name="Paused", slug="paused", owner=self.owner)
        for n in range(2):
            self.local_file(bucket, f"p{n}")
        self.mark(bucket)
        with mock.patch.object(bucket_lifecycle, "PURGE_BUDGET_SECONDS", 0), \
                mock.patch("toto.celery_utils.celery_available", return_value=False):
            with self.captureOnCommitCallbacks(execute=True):
                purge_bucket_task(bucket.pk, self.root.pk)
        bucket.refresh_from_db()
        self.assertIn("no worker took the rest", bucket.deletion_error)

    def test_a_purge_that_died_unseen_is_offered_again_after_a_while(self):
        from datetime import timedelta as td

        from .storage_adapters import purge_stalled, status_of

        bucket = Bucket.objects.create(name="Lost", slug="lost", owner=self.owner)
        self.mark(bucket)
        self.assertEqual(status_of(bucket), "deleting")
        self.assertFalse(purge_stalled(bucket))
        later = timezone.now() + td(minutes=31)
        self.assertTrue(purge_stalled(bucket, now=later))
        Bucket.objects.filter(pk=bucket.pk).update(
            deletion_requested_at=timezone.now() - td(hours=2))
        bucket.refresh_from_db()
        self.assertEqual(status_of(bucket), "delete_stalled")
        with override_settings(VAULT_PURGE_STALL_MINUTES=180):
            self.assertEqual(status_of(bucket), "deleting")
        # Confirming again restarts the clock, and resumes the job.
        with override_settings(VAULT_PURGE_INLINE=False), \
                mock.patch("toto.celery_utils.celery_available", return_value=False):
            with self.captureOnCommitCallbacks(execute=True):
                bucket_lifecycle.request_deletion(bucket, self.root, confirm_name="Lost")
        bucket.refresh_from_db()
        self.assertLess(timezone.now() - bucket.deletion_requested_at, td(minutes=1))
        self.delete(bucket)
        self.assertFalse(Bucket.objects.filter(pk=bucket.pk).exists())

    def test_a_second_run_that_finds_the_bucket_gone_records_nothing(self):
        bucket = Bucket.objects.create(name="Twice", slug="twice", owner=self.owner)
        self.mark(bucket)
        real_snapshot = bucket_lifecycle.snapshot

        def raced(b):
            facts = real_snapshot(b)
            # Another run (Delete confirmed again meanwhile) got there first.
            Bucket.objects.filter(pk=b.pk).delete()
            return facts

        with mock.patch.object(bucket_lifecycle, "snapshot", raced):
            result = bucket_lifecycle.purge_bucket(bucket.pk, actor_pk=self.root.pk)
        self.assertTrue(result["ok"])
        self.assertTrue(result["gone"])
        self.assertNotIn("VAULT.BUCKET.DELETED", audit_actions())
