"""Bucket peering — the credential models, before any transport exists.

Run only where a gate stanza names this module (the library pytest suite does
not collect vault Django tests):

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.vault.tests_peering
"""
import base64
import json
from unittest import mock

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from .admin import (
    BucketGrantAdmin, _decode_pairing_code, _pairing_code_for,
)
from .models import Bucket, StorageBackend
from .peering import (
    BUCKET_RIGHTS, BucketGrant, BucketPeer, PEER_PATH, has_bucket_right,
)

User = get_user_model()


def _bucket(owner, slug="exported", **kwargs):
    return Bucket.objects.create(name=slug, slug=slug, owner=owner, **kwargs)


class CapabilityResolverTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("exporter", password="x")
        cls.bucket = _bucket(cls.owner)

    def test_fresh_grant_denies_everything(self):
        # The empty capability set DENIES — the exact opposite of
        # VaultDirectory.allowed_users, whose empty whitelist allows everyone.
        # A grant that authenticates must still be able to do nothing.
        grant = BucketGrant.objects.create(label="peer", bucket=self.bucket)
        self.assertTrue(grant.can_be_used)
        for right in BUCKET_RIGHTS:
            self.assertFalse(has_bucket_right(grant, right), right)

    def test_unknown_right_raises(self):
        # A mistyped right that quietly returned False would be a gate nobody
        # can pass and nobody can find.
        grant = BucketGrant.objects.create(
            label="peer", bucket=self.bucket, may_list=True)
        with self.assertRaises(ValueError):
            has_bucket_right(grant, "may_copy")
        with self.assertRaises(ValueError):
            has_bucket_right(grant, "list")

    def test_granted_right_resolves(self):
        grant = BucketGrant.objects.create(
            label="peer", bucket=self.bucket, may_list=True)
        self.assertTrue(has_bucket_right(grant, "may_list"))
        self.assertFalse(has_bucket_right(grant, "may_download"))

    def test_no_grant_denies(self):
        self.assertFalse(has_bucket_right(None, "may_list"))

    def test_inactive_and_expired_deny_granted_rights(self):
        grant = BucketGrant.objects.create(
            label="peer", bucket=self.bucket, may_list=True, is_active=False)
        self.assertFalse(has_bucket_right(grant, "may_list"))
        grant.is_active = True
        grant.expires_at = timezone.now() - timezone.timedelta(minutes=1)
        self.assertFalse(has_bucket_right(grant, "may_list"))

    def test_default_expiry_is_seven_days(self):
        grant = BucketGrant.objects.create(label="peer", bucket=self.bucket)
        self.assertIsNotNone(grant.expires_at)
        delta = grant.expires_at - timezone.now()
        self.assertAlmostEqual(delta.total_seconds(), 7 * 86400, delta=120)


class GrantCredentialTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("exporter", password="x")
        cls.bucket = _bucket(cls.owner)

    def test_issue_api_key_stores_hash_only(self):
        grant = BucketGrant.objects.create(label="peer", bucket=self.bucket)
        raw = grant.issue_api_key()
        grant.save()
        stored = BucketGrant.objects.get(pk=grant.pk)
        self.assertNotIn(raw, stored.api_key_hash)
        self.assertTrue(stored.api_key_hash.startswith("pbkdf2_"))
        self.assertEqual(stored.api_key_hint, raw[-8:])
        self.assertTrue(stored.verify_api_key(raw))
        self.assertFalse(stored.verify_api_key(raw + "x"))
        self.assertFalse(stored.verify_api_key(""))

    def test_wrong_magic_token_never_reaches_pbkdf2(self):
        # The cheap check runs first: resolving a grant filters on the indexed
        # (grant_uid, magic_token) pair, so a guessed URL costs a miss, not
        # ~100ms of key stretching. (The SSO provider once saturated at
        # ~35 req/s because everything cheap did not run first.)
        grant = BucketGrant.objects.create(label="peer", bucket=self.bucket)
        grant.issue_api_key()
        grant.save()
        with mock.patch(
                "django.contrib.auth.hashers.check_password") as checker:
            found = BucketGrant.objects.filter(
                grant_uid=grant.grant_uid, magic_token="wrong").first()
            self.assertIsNone(found)
            checker.assert_not_called()

    def test_magic_token_is_high_entropy_and_unique(self):
        a = BucketGrant.objects.create(label="a", bucket=self.bucket)
        b = BucketGrant.objects.create(label="b", bucket=self.bucket)
        self.assertNotEqual(a.magic_token, b.magic_token)
        self.assertGreaterEqual(len(a.magic_token), 32)

    def test_clean_refuses_reexporting_a_mounted_bucket(self):
        # No daisy-chaining: a host must not relay a bucket whose bytes live
        # on a third host.
        peer = BucketPeer.objects.create(
            label="upstream", base_url="https://up.example.org",
            grant_uid="00000000-0000-0000-0000-000000000001",
            magic_token="t")
        mounted = _bucket(
            self.owner, slug="mounted",
            storage_backend=StorageBackend.REMOTE_TOTO, peer=peer)
        grant = BucketGrant(label="relay", bucket=mounted)
        with self.assertRaises(ValidationError):
            grant.clean()

    def test_base_path_matches_peer_path_constant(self):
        grant = BucketGrant.objects.create(label="peer", bucket=self.bucket)
        self.assertEqual(
            grant.base_path(),
            f"/vault/peer/{grant.grant_uid}/{grant.magic_token}/")
        self.assertIn("{grant_uid}", PEER_PATH)
        self.assertIn("{magic_token}", PEER_PATH)


class PeerCustodyTests(TestCase):
    def test_fernet_roundtrip_and_raw_absent_from_stored_bytes(self):
        peer = BucketPeer.objects.create(
            label="placidia", base_url="https://placidia.example.org",
            grant_uid="00000000-0000-0000-0000-000000000002",
            magic_token="tok")
        raw = "s3cret-api-key-value-abcdef"
        peer.set_api_key(raw)
        peer.save()
        stored = BucketPeer.objects.get(pk=peer.pk)
        self.assertNotIn(raw.encode(), bytes(stored.api_key_encrypted))
        self.assertEqual(stored.get_api_key(), raw)
        self.assertEqual(stored.api_key_hint, raw[-8:])

    def test_empty_key_reads_as_empty_string(self):
        peer = BucketPeer.objects.create(
            label="p", base_url="https://p.example.org",
            grant_uid="00000000-0000-0000-0000-000000000003",
            magic_token="tok2")
        self.assertEqual(peer.get_api_key(), "")

    def test_peer_with_buckets_is_protected(self):
        from django.db.models import ProtectedError
        owner = User.objects.create_user("mounter", password="x")
        peer = BucketPeer.objects.create(
            label="p", base_url="https://p2.example.org",
            grant_uid="00000000-0000-0000-0000-000000000004",
            magic_token="tok3")
        _bucket(owner, slug="mount",
                storage_backend=StorageBackend.REMOTE_TOTO, peer=peer)
        with self.assertRaises(ProtectedError):
            peer.delete()


class PairingCodeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("exporter", password="x")
        cls.bucket = _bucket(cls.owner)

    def test_roundtrip(self):
        grant = BucketGrant.objects.create(
            label="peer", bucket=self.bucket, may_list=True, may_download=True)
        raw = grant.issue_api_key()
        code = _pairing_code_for(grant, raw)
        payload = _decode_pairing_code(code)
        self.assertEqual(payload["v"], 1)
        self.assertEqual(payload["grant_uid"], str(grant.grant_uid))
        self.assertEqual(payload["magic_token"], grant.magic_token)
        self.assertEqual(payload["api_key"], raw)
        self.assertEqual(payload["bucket"], self.bucket.slug)
        self.assertEqual(payload["rights"], ["may_list", "may_download"])

    def test_garbage_and_wrong_version_refused_with_a_sentence(self):
        from django import forms
        with self.assertRaises(forms.ValidationError):
            _decode_pairing_code("not-base64!!")
        bad_version = base64.b64encode(
            json.dumps({"v": 2, "grant_uid": "g", "magic_token": "m",
                        "api_key": "k"}).encode()).decode()
        with self.assertRaises(forms.ValidationError):
            _decode_pairing_code(bad_version)
        missing = base64.b64encode(
            json.dumps({"v": 1, "grant_uid": "g"}).encode()).decode()
        with self.assertRaises(forms.ValidationError):
            _decode_pairing_code(missing)

    def test_admin_create_shows_code_once_and_stores_no_raw_key(self):
        grant = BucketGrant(label="peer", bucket=self.bucket, may_list=True)
        grant_admin = BucketGrantAdmin(BucketGrant, django_admin.site)
        request = mock.Mock(user=self.owner)
        with mock.patch.object(grant_admin, "message_user") as message_user:
            grant_admin.save_model(request, grant, form=None, change=False)
        message_user.assert_called_once()
        shown = str(message_user.call_args.args[1])
        payload = _decode_pairing_code(
            shown[shown.index("<code"):].split(">", 1)[1].split("<", 1)[0])
        stored = BucketGrant.objects.get(pk=grant.pk)
        self.assertTrue(stored.verify_api_key(payload["api_key"]))
        self.assertNotIn(payload["api_key"], stored.api_key_hash)
        # Re-saving (a change) shows nothing — the code exists exactly once.
        with mock.patch.object(grant_admin, "message_user") as message_user:
            grant_admin.save_model(request, stored, form=None, change=True)
        message_user.assert_not_called()

    def test_rotate_action_invalidates_old_key(self):
        grant = BucketGrant.objects.create(label="peer", bucket=self.bucket)
        old_raw = grant.issue_api_key()
        grant.save()
        grant_admin = BucketGrantAdmin(BucketGrant, django_admin.site)
        request = mock.Mock(user=self.owner)
        with mock.patch.object(grant_admin, "message_user") as message_user:
            grant_admin.rotate_api_key(
                request, BucketGrant.objects.filter(pk=grant.pk))
        message_user.assert_called_once()
        stored = BucketGrant.objects.get(pk=grant.pk)
        self.assertFalse(stored.verify_api_key(old_raw))
        self.assertIsNotNone(stored.key_rotated_at)
