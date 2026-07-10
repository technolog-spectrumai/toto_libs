from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform

from ..services import get_user_claims

User = get_user_model()


def _platform():
    p = Platform.objects.filter(active=True).first()
    if p:
        return p
    return Platform.objects.create(
        site_name="Test Site", author="Test", publication_year=2024, active=True
    )


class RolesClaimTests(TestCase):
    """The `roles` claim drives access for two relying parties: Grafana admits
    only `admin` (strict mapping) and Gitea requires `staff` to log in at all,
    so the exact list per user tier is contract, not implementation detail."""

    def setUp(self):
        _platform()

    def test_superuser_gets_admin_and_staff(self):
        user = User.objects.create_superuser("root", "root@example.com", "x")
        claims = get_user_claims(user, ["openid", "roles"])
        self.assertEqual(claims["roles"], ["admin", "staff"])
        self.assertTrue(claims["is_superuser"])

    def test_staff_gets_staff_only(self):
        user = User.objects.create_user("staffer", "s@example.com", "x", is_staff=True)
        claims = get_user_claims(user, ["openid", "roles"])
        self.assertEqual(claims["roles"], ["staff"])
        self.assertFalse(claims["is_superuser"])

    def test_plain_user_gets_viewer(self):
        user = User.objects.create_user("plain", "p@example.com", "x")
        claims = get_user_claims(user, ["openid", "roles"])
        self.assertEqual(claims["roles"], ["viewer"])
        self.assertFalse(claims["is_superuser"])

    def test_no_roles_without_scope(self):
        user = User.objects.create_superuser("root2", "root2@example.com", "x")
        claims = get_user_claims(user, ["openid", "email"])
        self.assertNotIn("roles", claims)


class DiscoveryDocumentTests(TestCase):
    def setUp(self):
        _platform()
        self.client = Client()

    def test_public_discovery_uses_request_host_everywhere(self):
        resp = self.client.get(reverse("sso:openid_configuration"))
        self.assertEqual(resp.status_code, 200)
        doc = resp.json()
        for key in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri"):
            self.assertTrue(
                doc[key].startswith("http://testserver/"),
                f"{key} unexpectedly not on the request host: {doc[key]}",
            )

    @override_settings(PLATFORM_DOMAIN="portal.example.com")
    def test_internal_discovery_splits_browser_and_server_endpoints(self):
        resp = self.client.get(reverse("sso:openid_configuration_internal"))
        self.assertEqual(resp.status_code, 200)
        doc = resp.json()
        # The one browser-facing endpoint is public…
        self.assertEqual(
            doc["authorization_endpoint"], "https://portal.example.com/sso/authorize/"
        )
        # …while the endpoints the relying party calls server-side stay on the
        # (internal) request host.
        for key in ("token_endpoint", "userinfo_endpoint", "jwks_uri"):
            self.assertTrue(doc[key].startswith("http://testserver/"), doc[key])

    @override_settings(PLATFORM_DOMAIN="portal.example.com")
    def test_internal_discovery_issuer_matches_public_doc(self):
        # goth validates the ID token's `iss` against the discovery `issuer`;
        # both variants must agree (they share get_issuer).
        internal = self.client.get(reverse("sso:openid_configuration_internal")).json()
        public = self.client.get(reverse("sso:openid_configuration")).json()
        self.assertEqual(internal["issuer"], public["issuer"])

    @override_settings(PLATFORM_DOMAIN="")
    def test_internal_discovery_falls_back_to_request_host(self):
        resp = self.client.get(reverse("sso:openid_configuration_internal"))
        doc = resp.json()
        self.assertEqual(
            doc["authorization_endpoint"], "http://testserver/sso/authorize/"
        )

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_internal_oidc_paths_exempt_from_ssl_redirect(self):
        # In PROD, SecurityMiddleware 301s plain-HTTP requests to https — but the
        # endpoints that sibling containers (grafana, gitea) call server-side on
        # gunicorn's plaintext :8000 must stay exempt (SECURE_REDIRECT_EXEMPT),
        # or SSO breaks with a redirect to a TLS URL on a plaintext port.
        for name in ("openid_configuration_internal", "jwks"):
            resp = self.client.get(reverse(f"sso:{name}"))
            self.assertEqual(resp.status_code, 200, name)
        # The public discovery document is browser/edge territory and is NOT
        # exempt — it still gets the https redirect.
        resp = self.client.get(reverse("sso:openid_configuration"))
        self.assertEqual(resp.status_code, 301)
