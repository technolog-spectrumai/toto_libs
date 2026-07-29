"""A consumer host signing a user in through a provider host, end to end.

Nothing about OIDC is stubbed: the authorization code is a real single-use row,
the ID token is really RS256-signed by a key held in a Gervazy strongbox, and
the consent page really renders. Only the socket between the two hosts is
replaced — see bridge.py.

**One process means one User table.** Real hosts have separate databases, so the
consumer's account for a person is a different row from the provider's. Here
they are necessarily the same row: the provider sends ``preferred_username``,
and ``_find_existing_user_for_claims`` finds the provider's own user by it. That
is a faithful model of one real case — a consumer that already had a matching
account — and it is what these tests assert against.

The other case, provisioning an account that did *not* exist, cannot be staged
here for the same reason, so it is covered in test_claims_mapping.py by driving
the claim-consumption directly with claims for an unknown subject.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.sso_master.models import SSOAuthorizationCode, SSOSubject

from ..bridge import FederationBrowser, provider_urlconf
from ..fixtures import federation_fixture

User = get_user_model()

PORTAL = "http://provider.test"
REDIRECT = "http://consumer.test/sso/callback/"


class FederatedLoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Once per class: the signing key costs a real Argon2id derivation.
        federation_fixture(portal_url=PORTAL, redirect_uri=REDIRECT)

    def setUp(self):
        self.portal_user = User.objects.create_user(
            "ada", "ada@example.org", "pw", first_name="Ada", last_name="Lovelace"
        )
        self.browser = FederationBrowser(portal_url=PORTAL)

    def test_happy_path_signs_the_user_in_on_the_consumer(self):
        response = self.browser.login(self.portal_user)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:dashboard"))
        self.assertEqual(
            int(self.browser.consumer.session["_auth_user_id"]), self.portal_user.pk
        )

        # An opaque subject was minted rather than the user's pk being exposed.
        subject = SSOSubject.objects.get(user=self.portal_user).subject
        self.assertNotEqual(str(subject), str(self.portal_user.pk))

        # The consumer really called the provider server-side, twice, in order.
        self.assertEqual(
            [(m, p) for m, p, _ in self.browser.loopback.calls],
            [("POST", "/sso/token/"), ("GET", "/sso/userinfo/")],
        )
        # ...and the single-use code it redeemed is burnt.
        self.assertTrue(SSOAuthorizationCode.objects.get().is_used)

    def test_the_authorization_code_cannot_be_redeemed_twice(self):
        self.browser.sign_in_at_provider(self.portal_user)
        started = self.browser.start_login()
        authorized = self.browser.authorize(started["Location"])

        first = self.browser.callback(authorized["Location"])
        self.assertEqual(first.status_code, 302)

        replay = FederationBrowser(portal_url=PORTAL)
        second = replay.callback(authorized["Location"])
        self.assertEqual(second.status_code, 400)
        self.assertNotIn("_auth_user_id", replay.consumer.session)

    @override_settings(TOTO_SSO_AUTO_PROVISION=True)
    def test_a_tampered_state_is_rejected(self):
        self.browser.sign_in_at_provider(self.portal_user)
        started = self.browser.start_login()
        authorized = self.browser.authorize(started["Location"])
        tampered = authorized["Location"].replace("state=", "state=x")

        response = self.browser.callback(tampered)

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("_auth_user_id", self.browser.consumer.session)
        # Rejected before the code was ever redeemed.
        self.assertEqual(self.browser.loopback.calls, [])

    def test_an_unregistered_redirect_uri_is_refused(self):
        # Exact-string matching: a trailing slash is a different URI. This is
        # the single most common federation misconfiguration.
        self.browser.sign_in_at_provider(self.portal_user)
        with provider_urlconf():
            response = self.browser.provider.get(reverse("sso:authorize"), {
                "response_type": "code",
                "client_id": "studio",
                "redirect_uri": REDIRECT.rstrip("/"),
                "scope": "openid email profile",
                "state": "s",
            })
        self.assertEqual(response.status_code, 400)

    def test_a_scope_the_client_was_not_granted_is_refused(self):
        self.browser.sign_in_at_provider(self.portal_user)
        with provider_urlconf():
            response = self.browser.provider.get(reverse("sso:authorize"), {
                "response_type": "code",
                "client_id": "studio",
                "redirect_uri": REDIRECT,
                "scope": "openid email profile roles secrets",
                "state": "s",
            })
        self.assertEqual(response.status_code, 400)


class UntrustedClientTests(TestCase):
    """A relying party that is not `trusted` sends every login through consent.

    Worth pinning: a toto-to-toto federation is provisioned trusted precisely so
    users are not asked to approve their own internal server on each sign-in.
    """

    @classmethod
    def setUpTestData(cls):
        federation_fixture(portal_url=PORTAL, redirect_uri=REDIRECT, trusted=False)

    def setUp(self):
        self.user = User.objects.create_user("bob", "bob@example.org", "pw")
        self.browser = FederationBrowser(portal_url=PORTAL)

    @override_settings(TOTO_SSO_AUTO_PROVISION=True)
    def test_consent_is_required_then_the_login_completes(self):
        self.browser.sign_in_at_provider(self.user)
        started = self.browser.start_login()

        without_consent = self.browser.authorize(started["Location"])
        self.assertEqual(without_consent.status_code, 200)  # the consent page

        with_consent = self.browser.authorize(started["Location"], approve_consent=True)
        self.assertEqual(with_consent.status_code, 302)
        response = self.browser.callback(with_consent["Location"])
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.browser.consumer.session)
