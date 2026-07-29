"""Properties the provider must hold because other hosts depend on it.

These are not OIDC conformance tests — `test_federated_login` covers the flow.
They pin the specific behaviours that, when they were absent, made the provider
able to take every consumer down with it.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.sso_master.models import SSOAccessToken, SSOAuthorizationCode, SSORelyingParty
from toto.sso_master.provisioning import create_relying_party, recreate_relying_party

from ..bridge import FederationBrowser, provider_urlconf
from ..fixtures import federation_fixture

User = get_user_model()

PORTAL = "http://provider.test"
REDIRECT = "http://consumer.test/sso/callback/"


class TokenEndpointCostTests(TestCase):
    """An unauthenticated caller must not be able to make the provider do work.

    `/sso/token/` is csrf-exempt and unauthenticated by construction, and
    verifying a client secret is PBKDF2 at Django's default 600k iterations —
    ~114 ms of CPU. It used to run that *before* looking at the authorization
    code, so anyone could spend a worker's time with nothing but a guessable
    client_id, and a few dozen requests per second saturated the whole host —
    not just SSO. The code must be checked first.
    """

    @classmethod
    def setUpTestData(cls):
        federation_fixture(portal_url=PORTAL, redirect_uri=REDIRECT)

    def _post_token(self, **overrides):
        payload = {
            "grant_type": "authorization_code",
            "code": "not-a-real-code",
            "redirect_uri": REDIRECT,
            "client_id": "studio",
            "client_secret": "shared-s3cret",
        }
        payload.update(overrides)
        with provider_urlconf():
            return self.client.post(reverse("sso:token"), payload)

    def test_an_unknown_code_is_refused_without_verifying_the_secret(self):
        with self.assertNumQueries(2):  # the client row and the code row, nothing more
            response = self._post_token()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_grant")

    def test_a_wrong_secret_with_no_valid_code_is_still_only_invalid_grant(self):
        # The point: a caller who does not hold a code never reaches the
        # expensive comparison at all, whatever secret they send.
        response = self._post_token(client_secret="wrong")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_grant")

    def test_a_wrong_secret_with_a_valid_code_is_still_rejected(self):
        # And the reorder must not have weakened anything: holding a code is not
        # sufficient, the secret is still verified before a token is issued.
        user = User.objects.create_user("ada", "ada@example.org", "pw")
        browser = FederationBrowser(portal_url=PORTAL)
        browser.sign_in_at_provider(user)
        started = browser.start_login()
        authorized = browser.authorize(started["Location"])
        code = authorized["Location"].split("code=")[1].split("&")[0]

        response = self._post_token(code=code, client_secret="wrong")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "invalid_client")
        self.assertFalse(SSOAuthorizationCode.objects.get(code=code).is_used)
        self.assertEqual(SSOAccessToken.objects.count(), 0)

    def test_a_code_issued_to_another_client_is_refused(self):
        create_relying_party(
            name="Other", client_id="other", redirect_uris=[REDIRECT],
            trusted=True, raw_secret="other-secret", force_recreate=True,
        )
        user = User.objects.create_user("bob", "bob@example.org", "pw")
        browser = FederationBrowser(portal_url=PORTAL)
        browser.sign_in_at_provider(user)
        started = browser.start_login()
        authorized = browser.authorize(started["Location"])
        code = authorized["Location"].split("code=")[1].split("&")[0]

        response = self._post_token(
            code=code, client_id="other", client_secret="other-secret"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_grant")


class ProvisioningIdempotencyTests(TestCase):
    """Re-registering a relying party must not revoke its live tokens.

    `ingress_all` runs from the container entrypoint on *every* start, and it
    provisions the built-in relying parties with `force_recreate=True`. When
    that meant `delete()`, `SSOAuthorizationCode.client` and
    `SSOAccessToken.client` cascaded — so an ordinary restart, or a restart
    loop, logged out every relying party and changed the row's UUID underneath
    them.
    """

    def setUp(self):
        self.provisioned = create_relying_party(
            name="Studio", client_id="studio", redirect_uris=[REDIRECT],
            trusted=True, raw_secret="s3cret",
        )
        self.rp = self.provisioned.relying_party
        self.user = User.objects.create_user("ada", "ada@example.org", "pw")
        self.token = SSOAccessToken.objects.create(
            client=self.rp, user=self.user, scope="openid"
        )

    def _reprovision(self, **overrides):
        kwargs = dict(
            name="Studio", client_id="studio", redirect_uris=[REDIRECT],
            trusted=True, raw_secret="s3cret", force_recreate=True,
        )
        kwargs.update(overrides)
        return create_relying_party(**kwargs)

    def test_reprovisioning_keeps_the_row_and_its_tokens(self):
        self._reprovision()

        self.assertEqual(SSORelyingParty.objects.filter(client_id="studio").count(), 1)
        self.assertEqual(SSORelyingParty.objects.get(client_id="studio").pk, self.rp.pk)
        self.assertTrue(SSOAccessToken.objects.filter(pk=self.token.pk).exists())

    def test_reprovisioning_applies_changed_settings(self):
        # Idempotent must still mean "make it match", not "leave it alone".
        new_uri = "http://consumer.test/sso/other-callback/"
        result = self._reprovision(redirect_uris=[new_uri], trusted=False,
                                   scopes="openid email profile roles")

        rp = result.relying_party
        self.assertEqual(rp.pk, self.rp.pk)
        self.assertEqual(rp.redirect_uri_list(), [new_uri])
        self.assertFalse(rp.trusted)
        self.assertEqual(rp.allowed_scopes, "openid email profile roles")

    def test_the_same_deployment_secret_keeps_working_across_a_reprovision(self):
        self._reprovision()
        rp = SSORelyingParty.objects.get(client_id="studio")
        self.assertTrue(rp.verify_client_secret("s3cret"))

    def test_registering_a_duplicate_without_the_flag_is_still_refused(self):
        from toto.sso_master.provisioning import RelyingPartyProvisioningError

        with self.assertRaises(RelyingPartyProvisioningError):
            create_relying_party(
                name="Studio", client_id="studio", redirect_uris=[REDIRECT],
            )

    def test_recreate_is_still_available_and_does_cascade(self):
        # The deliberate, rare case — a leaked secret that cannot be rotated on
        # the relying party's side — keeps its teeth.
        recreate_relying_party(
            client_id="studio", name="Studio", redirect_uris=[REDIRECT],
            trusted=True, raw_secret="fresh",
        )

        self.assertNotEqual(SSORelyingParty.objects.get(client_id="studio").pk, self.rp.pk)
        self.assertFalse(SSOAccessToken.objects.filter(pk=self.token.pk).exists())
