from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase, override_settings

from toto.core.models import Platform

from ..models import SSORelyingParty, SSOSigningKey
from ..services import get_user_claims


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
    WEKAN_ENABLED=False,
    SSO_VAULT_PASSWORD="",  # keep these tests signing-key-free (see class below)
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

    @override_settings(WEKAN_ENABLED=True, WEKAN_OIDC_CLIENT_SECRET="s3cret-wekan")
    def test_wekan_relying_party_provisioned(self):
        call_command("ingress_sso_master")
        rp = SSORelyingParty.objects.get(client_id="wekan")
        self.assertEqual(rp.name, "Wekan")
        self.assertTrue(rp.trusted)
        # Wekan's OIDC callback path is fixed by Wekan, not chosen by us.
        self.assertEqual(
            rp.redirect_uris, "https://portal.example.com/boards/_oauth/oidc")
        self.assertTrue(rp.verify_client_secret("s3cret-wekan"))

    @override_settings(WEKAN_ENABLED=True, WEKAN_OIDC_CLIENT_SECRET="s3cret-wekan")
    def test_wekan_asks_for_the_groups_scope(self):
        """`groups` is the access model, so its absence is a real failure.

        Django group membership is what decides who reaches the boards, and
        Wekan learns it from this claim (it maps `admin` to instance admin via
        OAUTH2_ADMIN_GROUPS). Dropping the scope would leave every signed-in
        user an ordinary member with nobody able to administer the instance.
        """
        call_command("ingress_sso_master")
        scopes = SSORelyingParty.objects.get(client_id="wekan").allowed_scopes.split()
        self.assertIn("groups", scopes)
        self.assertIn("roles", scopes)

    @override_settings(WEKAN_ENABLED=True, WEKAN_OIDC_CLIENT_SECRET="s3cret-wekan")
    def test_a_non_staff_user_can_sign_in_to_wekan(self):
        """The deliberate difference from Gitea, asserted end to end.

        Gitea restricts sign-in to staff — but that gate does NOT live on the
        relying party here; `provision_oauth.sh` writes `--required-claim
        roles=staff` into Gitea's own auth source. So there is no field on
        this model to assert the absence of, and a test that looked for one
        would pass no matter what.

        What can be checked is the thing that actually matters: an ordinary
        non-staff account gets a usable `roles` claim from this provider. If
        someone ever makes the provider withhold claims from non-staff — the
        way to break Wekan sign-in from this side — this fails.
        """
        call_command("ingress_sso_master")
        user = get_user_model().objects.create_user(
            username="ordinary", email="ordinary@example.com", password="x")
        self.assertFalse(user.is_staff)
        claims = get_user_claims(user, scopes=["openid", "roles", "groups"])
        self.assertNotIn("staff", claims.get("roles", []))
        self.assertEqual(claims.get("groups"), [])

    @override_settings(WEKAN_ENABLED=True, WEKAN_OIDC_CLIENT_SECRET="s3cret-wekan")
    def test_group_membership_reaches_wekan_as_a_claim(self):
        """Django groups are the access model; this is the wire they ride.

        Putting somebody in a group is the whole administrative act — there
        are no external accounts to provision instead — so the claim has to
        carry the membership or the act does nothing.
        """
        call_command("ingress_sso_master")
        user = get_user_model().objects.create_user(
            username="boarder", email="boarder@example.com", password="x")
        user.groups.add(Group.objects.create(name="boards"))
        claims = get_user_claims(user, scopes=["openid", "groups"])
        self.assertIn("boards", claims["groups"])

    @override_settings(WEKAN_ENABLED=True, WEKAN_OIDC_CLIENT_SECRET="")
    def test_wekan_skipped_without_secret(self):
        call_command("ingress_sso_master")
        self.assertFalse(SSORelyingParty.objects.filter(client_id="wekan").exists())

    @override_settings(WEKAN_ENABLED=True, WEKAN_OIDC_CLIENT_SECRET="s3",
                       PLATFORM_DOMAIN="")
    def test_wekan_skipped_without_platform_domain(self):
        call_command("ingress_sso_master")
        self.assertFalse(SSORelyingParty.objects.filter(client_id="wekan").exists())

    @override_settings(WEKAN_ENABLED=True, WEKAN_OIDC_CLIENT_SECRET="s3cret-wekan")
    def test_wekan_provisioning_is_idempotent(self):
        call_command("ingress_sso_master")
        call_command("ingress_sso_master")
        self.assertEqual(SSORelyingParty.objects.filter(client_id="wekan").count(), 1)

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


@override_settings(
    PLATFORM_DOMAIN="portal.example.com",
    GRAFANA_ENABLED=False,
    GITEA_ENABLED=False,
    SSO_VAULT_PASSWORD="test-vault-pass",
)
class SigningKeyEnsureTests(TestCase):
    """The ingress must guarantee an active RS256 signing key: without one every
    OIDC token exchange 500s (and the burnt single-use code turns the relying
    party's retry into a cryptic invalid_grant)."""

    def setUp(self):
        _platform()
        get_user_model().objects.create_superuser(
            "boss", "boss@example.com", "x"
        )

    def test_key_created_when_missing(self):
        call_command("ingress_sso_master")
        key = SSOSigningKey.objects.get(is_active=True)
        self.assertTrue(key.key_id.startswith("sso-auto-"))
        self.assertIsNotNone(key.encrypted_key)

    def test_second_run_keeps_existing_key(self):
        call_command("ingress_sso_master")
        first = SSOSigningKey.objects.get(is_active=True)
        call_command("ingress_sso_master")
        self.assertEqual(SSOSigningKey.objects.count(), 1)
        self.assertEqual(SSOSigningKey.objects.get(is_active=True).pk, first.pk)

    @override_settings(SSO_VAULT_PASSWORD="")
    def test_skipped_without_vault_password(self):
        call_command("ingress_sso_master")
        self.assertFalse(SSOSigningKey.objects.exists())
