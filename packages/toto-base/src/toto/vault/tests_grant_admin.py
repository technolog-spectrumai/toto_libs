"""The bucket-grant admin is a record, not a second door for shares
(2026-10-01, todo 29.10).

It minted pairing codes — on add, and on a "Rotate api key" action — and
showed them in an admin message, which Django's message storage may keep in a
cookie. Shares are made, and their keys rotated, in Storage → Management
(``share_views.py``) only. Here: no add and no rotate, every field read-only
but Active, a pointer to Management; unticking Active, or the revoke action,
is Management's Revoke, on the audit chain; a revoked share stays revoked;
nothing here mints a key or shows one.
"""

from unittest import mock

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from toto.audit.models import AuditRecord
from toto.vault import admin as vault_admin
from toto.vault.admin import BucketGrantAdmin
from toto.vault.models import Bucket
from toto.vault.peering import BucketGrant

User = get_user_model()

REVOKED = "VAULT.BUCKET.SHARE_REVOKED"
CHANGELIST = "admin:vault_bucketgrant_changelist"


@override_settings(VAULT_EXTERNAL_BUCKETS=True)
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = User.objects.create_superuser("root", "r@e.org", "pw")
        cls.bucket = Bucket.objects.create(name="Exported", slug="exported", owner=cls.root)
        cls.other = Bucket.objects.create(name="Other", slug="other", owner=cls.root)

    def setUp(self):
        self.grant, self.raw = self.make("placidia")
        self.client.force_login(self.root)

    def make(self, label, **fields):
        grant = BucketGrant(label=label, bucket=self.bucket, may_list=True,
                            created_by=self.root, **fields)
        raw = grant.issue_api_key()
        grant.save()
        return grant, raw

    def change_url(self, grant=None):
        return reverse("admin:vault_bucketgrant_change", args=[(grant or self.grant).pk])

    def fresh(self, grant=None):
        return BucketGrant.objects.get(pk=(grant or self.grant).pk)

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def assert_no_secret(self, text, grant=None, raw=None):
        grant = grant or self.fresh()
        for secret in (raw or self.raw, grant.api_key_hash, grant.magic_token, str(grant.grant_uid)):
            self.assertNotIn(secret, text)


class NoCodeHereTests(_Fixture):
    def test_nothing_is_added_here(self):
        self.assertFalse(BucketGrantAdmin(BucketGrant, django_admin.site)
                         .has_add_permission(RequestFactory().get("/")))
        self.assertEqual(self.client.get(reverse("admin:vault_bucketgrant_add")).status_code, 403)
        response = self.client.post(reverse("admin:vault_bucketgrant_add"),
                                    {"label": "x", "bucket": self.bucket.pk, "may_list": "on"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(BucketGrant.objects.count(), 1)

    def test_no_action_mints_a_key_and_revoke_is_the_only_one(self):
        request = RequestFactory().get("/")
        request.user = self.root
        actions = BucketGrantAdmin(BucketGrant, django_admin.site).get_actions(request)
        self.assertEqual(set(actions), {"revoke_shares"})
        self.assertFalse(hasattr(BucketGrantAdmin, "rotate_api_key"))
        # The wire format is peering's alone; the admin keeps no alias of it.
        self.assertFalse(hasattr(vault_admin, "_pairing_code_for"))
        self.assertFalse(hasattr(vault_admin, "pairing_code_for"))

    def test_the_list_says_where_shares_are_made_and_offers_no_add(self):
        response = self.client.get(reverse(CHANGELIST))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('data-testid="bucketgrant-made-in-management"', body)
        self.assertIn(f'href="{reverse("vault:manage")}"', body)
        self.assertNotIn(reverse("admin:vault_bucketgrant_add"), body)
        self.assertNotIn("rotate_api_key", body)
        self.assert_no_secret(body)

    def test_nothing_here_mints_a_key(self):
        other, _raw = self.make("emilia")
        with mock.patch.object(BucketGrant, "issue_api_key",
                               side_effect=AssertionError("the admin minted a key")):
            self.client.get(reverse(CHANGELIST))
            self.client.get(self.change_url())
            self.client.post(self.change_url(), {"_save": "Save"})
            self.client.post(reverse(CHANGELIST), {
                "action": "revoke_shares", "_selected_action": [other.pk], "index": "0"})
        self.assertFalse(self.fresh().is_active)
        self.assertFalse(self.fresh(other).is_active)


class ChangeFormTests(_Fixture):
    def test_only_active_is_a_field_and_the_form_says_where_shares_are_made(self):
        response = self.client.get(self.change_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context["adminform"].form.fields), {"is_active"})
        body = response.content.decode()
        self.assertIn("Storage → Management", body)
        self.assertNotIn(self.raw, body)
        self.assertNotIn(self.grant.api_key_hash, body)

    def test_a_post_changes_nothing_but_active(self):
        before = self.fresh()
        response = self.client.post(self.change_url(), {
            "_save": "Save", "is_active": "on", "label": "evil", "bucket": self.other.pk,
            "may_download": "on", "may_upload": "on", "may_delete": "on",
            "expires_at_0": "2099-01-01", "expires_at_1": "00:00:00",
            "api_key_hint": "x", "magic_token": "x"})
        self.assertEqual(response.status_code, 302)
        after = self.fresh()
        for field in ("label", "bucket_id", "may_list", "may_download", "may_upload",
                      "may_delete", "expires_at", "api_key_hash", "api_key_hint",
                      "magic_token", "is_active", "key_rotated_at"):
            self.assertEqual(getattr(after, field), getattr(before, field), field)
        self.assertTrue(after.verify_api_key(self.raw))
        self.assertFalse(AuditRecord.objects.filter(action=REVOKED).exists())
        for message in self.messages(response):
            self.assert_no_secret(message)

    def test_unticking_active_is_managements_revoke_on_the_audit_chain(self):
        response = self.client.post(self.change_url(), {"_save": "Save"}, follow=True)
        self.assertEqual(response.status_code, 200)
        grant = self.fresh()
        self.assertFalse(grant.is_active)
        self.assertFalse(grant.can_be_used)
        record = AuditRecord.objects.get(action=REVOKED)
        self.assertEqual(record.actor_user, self.root)
        self.assertEqual(record.metadata["share"], grant.pk)
        self.assertEqual(record.metadata["label"], "placidia")
        self.assert_no_secret(str(record.metadata), grant)
        for message in self.messages(response):
            self.assert_no_secret(message, grant)

    def test_a_revoked_share_stays_revoked(self):
        BucketGrant.objects.filter(pk=self.grant.pk).update(is_active=False)
        response = self.client.get(self.change_url())
        self.assertEqual(set(response.context["adminform"].form.fields), set())
        self.client.post(self.change_url(), {"_save": "Save", "is_active": "on"})
        self.assertFalse(self.fresh().is_active)
        self.assertFalse(AuditRecord.objects.filter(action=REVOKED).exists())


class RevokeActionTests(_Fixture):
    def post(self, *grants):
        return self.client.post(reverse(CHANGELIST), {
            "action": "revoke_shares", "_selected_action": [g.pk for g in grants],
            "index": "0"}, follow=True)

    def test_it_revokes_the_live_ones_and_records_each(self):
        second, _raw = self.make("emilia")
        gone, _raw = self.make("poseidon", is_active=False)
        response = self.post(self.grant, second, gone)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.fresh().is_active)
        self.assertFalse(self.fresh(second).is_active)
        self.assertEqual(sorted(AuditRecord.objects.filter(action=REVOKED)
                                .values_list("metadata__label", flat=True)),
                         ["emilia", "placidia"])
        messages = self.messages(response)
        self.assertIn("2 shares revoked: the other Zenobias can no longer use their buckets.",
                      messages)
        for message in messages:
            self.assert_no_secret(message)

    def test_shares_revoked_already_say_so(self):
        BucketGrant.objects.filter(pk=self.grant.pk).update(is_active=False)
        response = self.post(self.grant)
        self.assertIn("Nothing to revoke: those shares were revoked already.",
                      self.messages(response))
        self.assertFalse(AuditRecord.objects.filter(action=REVOKED).exists())

    def test_staff_without_the_superuser_flag_cannot_run_it(self):
        from django.contrib.auth.models import Permission

        staff = User.objects.create_user("staff", password="pw", is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(
            codename__in=["view_bucketgrant", "change_bucketgrant"]))
        self.client.force_login(staff)
        response = self.client.post(reverse(CHANGELIST), {
            "action": "revoke_shares", "_selected_action": [self.grant.pk], "index": "0"})
        self.assertIn(response.status_code, (302, 403))
        self.assertTrue(self.fresh().is_active)
