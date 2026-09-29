"""Registering relying parties from host configuration, and the service guards.

Meant for the gate's provider stanza. ``create_relying_party`` is the path
Grafana, Gitea and Wekan take on every container start; the refusals here are
the ones that keep that path from fighting federation pairing.
"""
from __future__ import annotations

import io

from django.core.management import CommandError, call_command
from django.test import RequestFactory, TestCase, override_settings

from toto.core.models import Platform

from ..models import SSOAccessToken, SSORelyingParty, SSOSigningKey
from ..provisioning import (
    RelyingPartyProvisioningError,
    create_relying_party,
    recreate_relying_party,
)
from .. import services

FAST_HASHING = override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])


@FAST_HASHING
class ProvisioningTests(TestCase):
    def _create(self, **kwargs):
        defaults = {"name": "Grafana", "client_id": "grafana",
                    "redirect_uris": ["https://p.test/grafana/cb", "https://p.test/alt"],
                    "raw_secret": "s1"}
        defaults.update(kwargs)
        return create_relying_party(**defaults)

    def test_a_confidential_party_gets_its_secret_once_and_every_uri(self):
        provisioned = self._create()
        party = provisioned.relying_party
        self.assertEqual(provisioned.client_secret, "s1")
        self.assertEqual(party.redirect_uri_list(),
                         ["https://p.test/grafana/cb", "https://p.test/alt"])
        self.assertTrue(party.verify_client_secret("s1"))
        self.assertNotIn("s1", party.client_secret_hash)

    def test_a_public_party_has_no_secret(self):
        provisioned = self._create(client_id="spa", public=True, raw_secret=None)
        self.assertIsNone(provisioned.client_secret)
        self.assertEqual(provisioned.relying_party.client_secret_hash, "")

    def test_an_existing_client_id_is_refused_without_force(self):
        self._create()
        with self.assertRaises(RelyingPartyProvisioningError):
            self._create()

    def test_force_updates_in_place_and_keeps_live_tokens(self):
        from django.contrib.auth import get_user_model

        party = self._create().relying_party
        user = get_user_model().objects.create_user("u", password="pw")
        token = SSOAccessToken.objects.create(client=party, user=user, scope="openid")

        again = self._create(force_recreate=True, trusted=True,
                             redirect_uris=["https://p.test/new"]).relying_party

        self.assertEqual(again.pk, party.pk)
        self.assertTrue(again.trusted)
        self.assertEqual(again.redirect_uri_list(), ["https://p.test/new"])
        self.assertTrue(SSOAccessToken.objects.filter(pk=token.pk).exists())
        # The same deployment secret again is a no-op, not a rotation.
        self.assertIsNone(again.previous_secret_expires_at)

    def test_host_config_never_overwrites_a_paired_registration(self):
        SSORelyingParty.objects.create(name="Studio", client_id="studio",
                                       redirect_uris="https://studio.test/cb",
                                       pairing_managed=True)
        with self.assertRaises(RelyingPartyProvisioningError) as ctx:
            self._create(client_id="studio", force_recreate=True)
        self.assertIn("federation pairing", str(ctx.exception))
        self.assertEqual(SSORelyingParty.objects.get(client_id="studio").redirect_uris,
                         "https://studio.test/cb")

    def test_recreate_is_the_deliberate_way_to_revoke_everything(self):
        from django.contrib.auth import get_user_model

        party = self._create().relying_party
        user = get_user_model().objects.create_user("u", password="pw")
        SSOAccessToken.objects.create(client=party, user=user, scope="openid")

        fresh = recreate_relying_party(client_id="grafana", name="Grafana",
                                       redirect_uris=["https://p.test/cb"]).relying_party

        self.assertNotEqual(fresh.pk, party.pk)
        self.assertFalse(SSOAccessToken.objects.exists())

    def test_the_command_prints_the_secret_once_or_says_there_is_none(self):
        out = io.StringIO()
        call_command("create_sso_relying_party", name="Tool", client_id="tool",
                     redirect_uri=["https://t.test/cb"], raw_secret="tool-secret",
                     stdout=out)
        self.assertIn("client_secret: tool-secret", out.getvalue())

        out = io.StringIO()
        call_command("create_sso_relying_party", name="SPA", client_id="spa2",
                     redirect_uri=["https://s.test/cb"], public=True, stdout=out)
        self.assertIn("public relying party; use PKCE", out.getvalue())

        with self.assertRaises(CommandError):
            call_command("create_sso_relying_party", name="Tool", client_id="tool",
                         redirect_uri=["https://t.test/cb"], stdout=io.StringIO())


class ServiceGuardTests(TestCase):
    def test_no_active_platform_is_a_clear_error(self):
        Platform.objects.all().delete()
        with self.assertRaisesMessage(RuntimeError, "No active Platform"):
            services.get_issuer()

    def test_no_domain_and_no_request_cannot_name_an_issuer(self):
        Platform.objects.create(site_name="P", author="T", publication_year=2026,
                                active=True, domain="")
        with self.assertRaises(RuntimeError):
            services.get_issuer()
        request = RequestFactory().get("/", HTTP_HOST="idp.test")
        self.assertEqual(services.get_issuer(request), "http://idp.test")

    def test_no_signing_key_names_the_command_to_run(self):
        with self.assertRaisesMessage(RuntimeError, "create_sso_signing_key"):
            services.get_active_signing_key()

    def test_a_key_row_without_its_private_half_is_refused(self):
        SSOSigningKey.objects.create(key_id="orphan", public_key_pem="x", is_active=True)
        with self.assertRaisesMessage(RuntimeError, "no linked EncryptedPrivateKey"):
            services.get_signing_private_key_pem()

    def test_only_rsa_keys_are_published(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        pem = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        SSOSigningKey.objects.create(key_id="ec", public_key_pem=pem, is_active=True)
        with self.assertRaisesMessage(RuntimeError, "RSA"):
            services.get_jwks()

    def test_retired_keys_leave_the_jwks(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        def pem():
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            return key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo).decode()

        SSOSigningKey.objects.create(key_id="old", public_key_pem=pem(), is_active=False)
        SSOSigningKey.objects.create(key_id="new", public_key_pem=pem(), is_active=True)

        (published,) = services.get_jwks()["keys"]
        self.assertEqual((published["kid"], published["kty"], published["alg"],
                          published["e"]), ("new", "RSA", "RS256", "AQAB"))

    def test_base64url_uint_has_no_padding(self):
        self.assertEqual(services.base64url_uint(65537), "AQAB")
        self.assertEqual(services.base64url_uint(1), "AQ")


@FAST_HASHING
@override_settings(SSO_VAULT_PASSWORD="", GRAFANA_ENABLED=True,
                   GRAFANA_OIDC_CLIENT_SECRET="g-secret", GITEA_ENABLED=False,
                   WEKAN_ENABLED=False)
class IngressGuardTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="P", author="T", publication_year=2026,
                                active=True)

    @override_settings(PLATFORM_DOMAIN="")
    def test_no_public_domain_skips_grafana_with_a_warning(self):
        out = io.StringIO()
        call_command("ingress_sso_master", stdout=out)
        self.assertFalse(SSORelyingParty.objects.exists())
        self.assertIn("PLATFORM_DOMAIN is empty", out.getvalue())

    @override_settings(PLATFORM_DOMAIN="p.test")
    def test_a_paired_row_in_the_way_is_reported_and_the_rest_continues(self):
        SSORelyingParty.objects.create(name="Grafana", client_id="grafana",
                                       redirect_uris="https://x.test/cb",
                                       pairing_managed=True)
        out = io.StringIO()
        call_command("ingress_sso_master", stdout=out)
        self.assertIn("_provision_grafana failed", out.getvalue())
        self.assertEqual(SSORelyingParty.objects.get(client_id="grafana").redirect_uris,
                         "https://x.test/cb")

    @override_settings(PLATFORM_DOMAIN="p.test")
    def test_no_vault_password_means_no_key_and_says_so(self):
        out = io.StringIO()
        call_command("ingress_sso_master", stdout=out)
        self.assertFalse(SSOSigningKey.objects.exists())
        self.assertIn("SSO_VAULT_PASSWORD is unset", out.getvalue())
        self.assertTrue(SSORelyingParty.objects.get(client_id="grafana")
                        .verify_client_secret("g-secret"))

    @override_settings(PLATFORM_DOMAIN="p.test", SSO_VAULT_PASSWORD="pw-for-the-vault")
    def test_a_vault_password_without_a_superuser_cannot_mint_a_key(self):
        out = io.StringIO()
        call_command("ingress_sso_master", stdout=out)
        self.assertFalse(SSOSigningKey.objects.exists())
        self.assertIn("no superuser to own the vault", out.getvalue())
