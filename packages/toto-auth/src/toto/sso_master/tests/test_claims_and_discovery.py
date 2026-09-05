from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform

from ..models import SSOAuthorizationCode, SSORelyingParty
from ..services import get_user_claims

User = get_user_model()


def _platform():
    p = Platform.objects.filter(active=True).first()
    if p:
        return p
    return Platform.objects.create(
        site_name="Test Site", author="Test", publication_year=2024, active=True
    )



class IssuerIsAUrlTests(TestCase):
    """The issuer must be a URL, whatever an operator typed into Platform.domain.

    It was returned verbatim until 2026-09-06, so a local stack advertised
    `"issuer": "localhost"` next to endpoints reading `https://localhost/...`,
    and put the same bare string in every ID token's `iss`. OIDC requires a
    URL there, and a relying party validates the `iss` it receives against the
    issuer it discovered — so a strict client refuses the token and the
    sign-in dies at the callback.
    """

    def _platform(self, domain):
        Platform.objects.all().delete()
        return Platform.objects.create(
            site_name="T", author="A", publication_year=2026,
            active=True, domain=domain)

    def test_a_bare_hostname_gains_a_scheme(self):
        from toto.sso_master.services import get_issuer

        self._platform("localhost")
        self.assertEqual(get_issuer(), "https://localhost")

    def test_an_explicit_scheme_is_kept(self):
        """http:// included — a host that means it must not be rewritten."""
        from toto.sso_master.services import get_issuer

        self._platform("http://dev.example")
        self.assertEqual(get_issuer(), "http://dev.example")
        self._platform("https://www.example.pl")
        self.assertEqual(get_issuer(), "https://www.example.pl")

    def test_it_normalises_the_same_way_as_the_public_base_url(self):
        """The two SHAPES must match, even though the sources differ.

        `get_issuer` reads `Platform.domain` (a database row) and
        `get_public_base_url` reads `settings.PLATFORM_DOMAIN` (deploy-time
        env). They are configured to agree and a client compares what they
        produce, so a scheme rule in one and not the other is the bug this
        pair had — asserted by feeding both the same input rather than by
        pretending they read the same place.
        """
        from toto.sso_master.services import get_issuer, get_public_base_url

        for domain in ("localhost", "https://www.example.pl"):
            with self.subTest(domain=domain):
                self._platform(domain)
                with override_settings(PLATFORM_DOMAIN=domain):
                    self.assertEqual(get_issuer(), get_public_base_url())

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


class GroupsClaimTests(TestCase):
    """The `groups` claim is how access to Wekan and lakeFS is governed.

    There are no external accounts on this platform: every account belongs to
    somebody at the company, so "who may reach the boards" is a membership
    question rather than an account-type one. An administrator adds or removes
    a group; the next token carries the change.

    Separate from `roles` on purpose. `roles` is derived from
    is_staff/is_superuser and says what somebody IS; `groups` says which rooms
    they have been let into, and moves without touching anybody's staff flag.
    """

    def setUp(self):
        _platform()

    def test_membership_is_reported_verbatim(self):
        from django.contrib.auth.models import Group

        user = User.objects.create_user("member", "m@example.com", "x")
        for name in ("boards", "datasets"):
            user.groups.add(Group.objects.create(name=name))

        claims = get_user_claims(user, ["openid", "groups"])

        self.assertEqual(claims["groups"], ["boards", "datasets"])

    def test_no_membership_is_an_empty_list_not_a_missing_key(self):
        """A relying party reading `claims["groups"]` must not KeyError on
        somebody who is in no group — that is the common case for a new
        account, and it means "no access", not "unknown"."""
        user = User.objects.create_user("loner", "l@example.com", "x")

        claims = get_user_claims(user, ["openid", "groups"])

        self.assertEqual(claims["groups"], [])

    def test_a_superuser_always_carries_admin(self):
        """Wekan maps `admin` to instance administrator via
        OAUTH2_ADMIN_GROUPS. Stated in code rather than seeded as a real group,
        so it cannot drift from the `roles` claim, which already says admin for
        exactly these people."""
        user = User.objects.create_superuser("root3", "r3@example.com", "x")

        claims = get_user_claims(user, ["openid", "groups"])

        self.assertIn("admin", claims["groups"])

    def test_staff_alone_does_not_grant_a_group(self):
        """The whole point of a second claim: being staff is not being let into
        a room. A staff member with no group memberships reaches no boards."""
        user = User.objects.create_user("staffer2", "s2@example.com", "x",
                                        is_staff=True)

        claims = get_user_claims(user, ["openid", "groups"])

        self.assertEqual(claims["groups"], [])

    def test_no_groups_without_the_scope(self):
        from django.contrib.auth.models import Group

        user = User.objects.create_user("scoped", "sc@example.com", "x")
        user.groups.add(Group.objects.create(name="boards"))

        claims = get_user_claims(user, ["openid", "email", "roles"])

        self.assertNotIn("groups", claims)


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

class TokenCodeAtomicityTests(TestCase):
    """A crash mid-exchange (the missing-signing-key incident) must not consume
    the single-use authorization code: goth/x-oauth2 retries the POST, and a
    burnt code turns the real error into a cryptic invalid_grant."""

    REDIRECT = "https://portal.example.com/gitea/user/oauth2/portal-sso/callback"

    def setUp(self):
        _platform()
        self.user = User.objects.create_user("alice", password="x")
        self.rp = SSORelyingParty.objects.create(
            name="Gitea", client_id="gitea",
            redirect_uris=self.REDIRECT, allowed_scopes="openid roles",
        )
        self.rp.set_client_secret("s3")
        self.rp.save()

    def _mint_code(self):
        return SSOAuthorizationCode.objects.create(
            client=self.rp, user=self.user,
            redirect_uri=self.REDIRECT, scope="openid roles", nonce="",
        )

    def _exchange(self, code, client=None):
        return (client or self.client).post(reverse("sso:token"), {
            "grant_type": "authorization_code",
            "code": code.code,
            "redirect_uri": self.REDIRECT,
            "client_id": "gitea",
            "client_secret": "s3",
        })

    def test_crash_rolls_back_code_consumption(self):
        code = self._mint_code()
        crashing = Client(raise_request_exception=False)
        with mock.patch(
            "toto.sso_master.views.build_id_token",
            side_effect=RuntimeError("no signing key"),
        ):
            resp = self._exchange(code, client=crashing)
        self.assertEqual(resp.status_code, 500)
        code.refresh_from_db()
        self.assertFalse(code.is_used)

        # …so the relying party's retry succeeds once the cause is fixed.
        with mock.patch(
            "toto.sso_master.views.build_id_token", return_value="x.y.z"
        ):
            resp = self._exchange(code)
        self.assertEqual(resp.status_code, 200)
        code.refresh_from_db()
        self.assertTrue(code.is_used)

    def test_used_code_is_rejected(self):
        code = self._mint_code()
        with mock.patch(
            "toto.sso_master.views.build_id_token", return_value="x.y.z"
        ):
            self.assertEqual(self._exchange(code).status_code, 200)
            resp = self._exchange(code)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "invalid_grant")


class SslRedirectExemptTests(TestCase):
    def setUp(self):
        _platform()

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
