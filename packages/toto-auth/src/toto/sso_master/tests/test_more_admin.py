"""The relying-party admin: pairing pages, credential displays, read-only rows.

Meant for the gate's provider stanza (toto.sso_master.testing.settings), which
mounts the admin at /admin/. The hardening *contract* — that no admin can mint
a code or token for somebody else — is asserted in the federation suite; this
module covers the pages and displays an operator actually uses.
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform

from ..admin import SSORelyingPartyAdmin, _fingerprint, _suggested_host
from ..models import (
    SSOAccessToken,
    SSOAuthorizationCode,
    SSOFederationInvite,
    SSORelyingParty,
    SSOSigningKey,
    SSOSubject,
)

User = get_user_model()
FAST_HASHING = override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])


class HelperTests(TestCase):
    def test_a_fingerprint_identifies_without_revealing(self):
        value = "a-very-secret-access-token"
        print_ = _fingerprint(value)
        self.assertEqual(print_, _fingerprint(value))
        self.assertNotEqual(print_, _fingerprint(value + "x"))
        self.assertEqual(len(print_), 9)
        self.assertNotIn("secret", print_)
        self.assertEqual(_fingerprint(""), "—")

    def test_a_suggested_host_is_a_hostname_or_nothing(self):
        self.assertEqual(_suggested_host("https://Studio.Example.com:8443/sso/cb"),
                         "studio.example.com")
        self.assertEqual(_suggested_host("delta.test"), "delta.test")
        for hostile in ("", None, "evil.test<script>", "trust me, this is fine",
                        "-leading.test", "a" * 254):
            with self.subTest(value=hostile):
                self.assertEqual(_suggested_host(hostile), "")


@FAST_HASHING
class DisplayTests(TestCase):
    def setUp(self):
        self.admin = SSORelyingPartyAdmin(SSORelyingParty, django_admin.site)
        self.party = SSORelyingParty.objects.create(
            name="Studio", client_id="studio", redirect_uris="https://studio.test/cb",
            pairing_managed=True)

    def test_secret_state_tells_the_rotation_story(self):
        self.assertEqual(self.admin.secret_state(self.party), "not set")

        self.party.rotate_client_secret("one")
        self.assertTrue(self.admin.secret_state(self.party).startswith("set · rotated "))

        self.party.secret_proven_at = timezone.now()
        self.assertIn("confirmed", self.admin.secret_state(self.party))

        self.party.rotate_client_secret("two")
        self.assertIn("previous valid until", self.admin.secret_state(self.party))
        self.assertNotIn("still using it", self.admin.secret_state(self.party))
        self.party.previous_secret_used_at = timezone.now()
        self.assertIn("the other side is still using it",
                      self.admin.secret_state(self.party))

    def test_pairing_state_and_origin(self):
        self.assertEqual(self.admin.pairing_state(self.party), "Invited, not yet redeemed.")
        self.party.paired_at = timezone.now()
        self.assertTrue(self.admin.pairing_state(self.party).startswith("Paired "))
        self.assertEqual(self.admin.origin(self.party), "paired")
        self.party.pairing_managed = False
        self.assertIn("host configuration", self.admin.pairing_state(self.party))
        self.assertEqual(self.admin.origin(self.party), "host config")

    def test_last_seen_is_the_newest_token(self):
        self.assertEqual(self.admin.last_seen(self.party), "never")
        user = User.objects.create_user("u", password="pw")
        SSOAccessToken.objects.create(client=self.party, user=user, scope="openid")
        self.assertRegex(self.admin.last_seen(self.party), r"^\d{4}-\d\d-\d\d \d\d:\d\d$")


@FAST_HASHING
@override_settings(PLATFORM_DOMAIN="provider.test")
class PairingPageTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="P", author="T", publication_year=2026,
                                active=True)
        self.root = User.objects.create_superuser("root", "r@x.test", "pw")
        self.client.force_login(self.root)
        self.invite_url = reverse("admin:sso_master_ssorelyingparty_invite")

    def test_the_invite_page_prefills_only_a_well_formed_host(self):
        good = self.client.get(self.invite_url, {"host": "https://delta.test/sso/cb"})
        bad = self.client.get(self.invite_url, {"host": "click here to win"})
        self.assertEqual(good.context["form"]["expected_host"], "delta.test")
        self.assertEqual(bad.context["form"]["expected_host"], "")
        # Authority is never taken from the link.
        forced = self.client.get(self.invite_url, {"host": "d.test", "roles": "1",
                                                   "trusted": "0"})
        self.assertFalse(forced.context["form"]["roles"])
        self.assertTrue(forced.context["form"]["trusted"])

    def test_posting_the_invite_mints_a_code_once(self):
        response = self.client.post(self.invite_url, {
            "expected_host": "delta.test", "roles": "on", "trusted": "on",
            "ttl_minutes": "0"})
        minted = response.context["minted"]
        self.assertTrue(minted["ticket"])
        # Drawn in the browser from the ticket (2026-10-01, 37c.30).
        self.assertNotIn("qr", minted)
        body = response.content.decode()
        self.assertIn("vendor/qrcodejs/qrcode.min.js", body)
        self.assertIn('id="pairing-qr"', body)
        self.assertIn(minted["ticket"], body)
        self.assertNotIn("data:image/png;base64,", body)
        self.assertEqual(minted["scopes"], "openid email profile roles")
        invite = SSOFederationInvite.objects.get()
        self.assertEqual(invite.expected_host, "delta.test")
        self.assertEqual(response.context["form"]["ttl_minutes"], 1)  # clamped up
        self.assertNotIn(minted["ticket"], invite.secret_sha256)

    def test_an_invite_without_a_host_is_explained_not_minted(self):
        response = self.client.post(self.invite_url, {"expected_host": "  "})
        self.assertIn("hostname is required", response.context["error"])
        self.assertFalse(SSOFederationInvite.objects.exists())

    def test_re_pairing_defaults_to_what_the_registration_already_has(self):
        party = SSORelyingParty.objects.create(
            name="Studio", client_id="studio", redirect_uris="https://studio.test/cb",
            allowed_scopes="openid roles", trusted=False, pairing_managed=True)
        url = reverse("admin:sso_master_ssorelyingparty_repair", args=[party.pk])
        form = self.client.get(url).context["form"]
        self.assertEqual((form["expected_host"], form["roles"], form["trusted"]),
                         ("studio.test", True, False))

    def test_re_pairing_an_unknown_row_goes_back_to_the_list(self):
        url = reverse("admin:sso_master_ssorelyingparty_repair",
                      args=["00000000-0000-0000-0000-000000000000"])
        response = self.client.get(url)
        self.assertRedirects(response,
                             reverse("admin:sso_master_ssorelyingparty_changelist"),
                             fetch_redirect_response=False)

    def test_staff_without_the_model_permission_cannot_pair(self):
        staff = User.objects.create_user("ops", "o@x.test", "pw", is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename="view_ssorelyingparty"))
        self.client.force_login(staff)
        response = self.client.post(self.invite_url, {"expected_host": "delta.test"})
        self.assertRedirects(response, reverse("admin:index"),
                             fetch_redirect_response=False)
        self.assertFalse(SSOFederationInvite.objects.exists())

    def test_the_change_page_offers_re_pair_only_for_paired_rows(self):
        paired = SSORelyingParty.objects.create(name="A", client_id="a",
                                                redirect_uris="https://a.test/cb",
                                                pairing_managed=True)
        sidecar = SSORelyingParty.objects.create(name="Gitea", client_id="gitea",
                                                 redirect_uris="https://g.test/cb")
        change = lambda p: self.client.get(
            reverse("admin:sso_master_ssorelyingparty_change", args=[p.pk]))
        self.assertIn("repair_url", change(paired).context)
        self.assertNotIn("repair_url", change(sidecar).context)
        self.assertIn("test_login_url", change(sidecar).context)


@FAST_HASHING
class ReadOnlyRowsTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser("root", "r@x.test", "pw")
        self.request = RequestFactory().get("/admin/")
        self.request.user = self.root
        self.party = SSORelyingParty.objects.create(name="A", client_id="a",
                                                    redirect_uris="https://a.test/cb")

    def model_admin(self, model):
        return django_admin.site._registry[model]

    def test_credentials_cannot_be_added_or_edited_even_by_a_superuser(self):
        for model in (SSOAuthorizationCode, SSOAccessToken, SSOFederationInvite):
            with self.subTest(model=model.__name__):
                admin_ = self.model_admin(model)
                self.assertFalse(admin_.has_add_permission(self.request))
                self.assertFalse(admin_.has_change_permission(self.request))

    def test_signing_keys_and_subjects_can_be_neither_added_nor_deleted(self):
        for model in (SSOSigningKey, SSOSubject):
            with self.subTest(model=model.__name__):
                admin_ = self.model_admin(model)
                self.assertFalse(admin_.has_add_permission(self.request))
                self.assertFalse(admin_.has_delete_permission(self.request))
        self.assertIn("is_active", self.model_admin(SSOSigningKey).readonly_fields)

    def test_code_and_token_values_never_reach_the_page(self):
        user = User.objects.create_user("u", password="pw")
        code = SSOAuthorizationCode.objects.create(client=self.party, user=user,
                                                   redirect_uri="https://a.test/cb",
                                                   scope="openid")
        token = SSOAccessToken.objects.create(client=self.party, user=user, scope="openid")
        self.client.force_login(self.root)
        for obj, secret in ((code, code.code), (token, token.token)):
            with self.subTest(model=type(obj).__name__):
                meta = obj._meta
                url = reverse(f"admin:{meta.app_label}_{meta.model_name}_change",
                              args=[obj.pk])
                body = self.client.get(url).content.decode()
                self.assertNotIn(secret, body)
                self.assertIn(_fingerprint(secret), body)

    def test_the_revoke_actions_touch_only_live_rows(self):
        user = User.objects.create_user("u", password="pw")
        live = SSOAccessToken.objects.create(client=self.party, user=user, scope="openid")
        old = SSOAccessToken.objects.create(client=self.party, user=user, scope="openid")
        earlier = timezone.now() - timedelta(days=1)
        SSOAccessToken.objects.filter(pk=old.pk).update(revoked_at=earlier)
        self.client.force_login(self.root)

        self.client.post(reverse("admin:sso_master_ssoaccesstoken_changelist"),
                         {"action": "revoke_tokens",
                          "_selected_action": [live.pk, old.pk]})

        live.refresh_from_db()
        old.refresh_from_db()
        self.assertIsNotNone(live.revoked_at)
        self.assertEqual(old.revoked_at, earlier)

        pending = SSOFederationInvite.objects.create(
            relying_party=self.party, secret_sha256="a" * 64, expected_host="x.test",
            expires_at=timezone.now() + timedelta(minutes=5))
        redeemed = SSOFederationInvite.objects.create(
            relying_party=self.party, secret_sha256="b" * 64, expected_host="y.test",
            expires_at=timezone.now() + timedelta(minutes=5), redeemed_at=timezone.now())
        self.client.post(reverse("admin:sso_master_ssofederationinvite_changelist"),
                         {"action": "revoke",
                          "_selected_action": [pending.pk, redeemed.pk]})
        pending.refresh_from_db()
        redeemed.refresh_from_db()
        self.assertEqual((pending.state, redeemed.state), ("revoked", "redeemed"))
