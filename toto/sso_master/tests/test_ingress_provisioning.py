from __future__ import annotations

from django.core.management import call_command
from django.test import TestCase, override_settings

from toto.core.models import Platform

from ..models import SSORelyingParty


def _platform():
    p = Platform.objects.filter(active=True).first()
    if p:
        return p
    return Platform.objects.create(
        site_name="Test Site", author="Test", publication_year=2024, active=True
    )


@override_settings(
    PLATFORM_DOMAIN="portal.example.com",
    GRAFANA_ENABLED=False,
    GITEA_ENABLED=False,
)
class IngressProvisioningTests(TestCase):
    def setUp(self):
        _platform()

    def test_disabled_services_provision_nothing(self):
        call_command("ingress_sso_master")
        self.assertFalse(SSORelyingParty.objects.exists())

    @override_settings(GITEA_ENABLED=True, GITEA_OIDC_CLIENT_SECRET="s3cret-gitea",
                       GITEA_OIDC_SOURCE_NAME="portal-sso")
    def test_gitea_relying_party_provisioned(self):
        call_command("ingress_sso_master")
        rp = SSORelyingParty.objects.get(client_id="gitea")
        self.assertEqual(rp.name, "Gitea")
        self.assertTrue(rp.trusted)
        # The redirect URI embeds the Gitea auth-source name created by
        # provision_oauth.sh — the two sides must agree on it.
        self.assertEqual(
            rp.redirect_uris,
            "https://portal.example.com/gitea/user/oauth2/portal-sso/callback",
        )
        self.assertIn("roles", rp.allowed_scopes.split())
        self.assertTrue(rp.verify_client_secret("s3cret-gitea"))

    @override_settings(GITEA_ENABLED=True, GITEA_OIDC_CLIENT_SECRET="s3cret-gitea")
    def test_gitea_provisioning_is_idempotent(self):
        call_command("ingress_sso_master")
        call_command("ingress_sso_master")
        self.assertEqual(SSORelyingParty.objects.filter(client_id="gitea").count(), 1)

    @override_settings(GITEA_ENABLED=True, GITEA_OIDC_CLIENT_SECRET="")
    def test_gitea_skipped_without_secret(self):
        call_command("ingress_sso_master")
        self.assertFalse(SSORelyingParty.objects.filter(client_id="gitea").exists())

    @override_settings(GITEA_ENABLED=True, GITEA_OIDC_CLIENT_SECRET="s3",
                       PLATFORM_DOMAIN="")
    def test_gitea_skipped_without_platform_domain(self):
        call_command("ingress_sso_master")
        self.assertFalse(SSORelyingParty.objects.filter(client_id="gitea").exists())

    @override_settings(GRAFANA_ENABLED=True, GRAFANA_OIDC_CLIENT_SECRET="s3cret-grafana")
    def test_grafana_relying_party_provisioned(self):
        call_command("ingress_sso_master")
        rp = SSORelyingParty.objects.get(client_id="grafana")
        self.assertEqual(rp.name, "Grafana")
        self.assertTrue(rp.trusted)
        self.assertEqual(
            rp.redirect_uris,
            "https://portal.example.com/grafana/login/generic_oauth",
        )
        self.assertTrue(rp.verify_client_secret("s3cret-grafana"))
