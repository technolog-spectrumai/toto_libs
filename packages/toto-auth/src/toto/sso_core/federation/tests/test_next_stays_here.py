"""A consumer's sign-in and sign-out send the member nowhere off-site
(2026-09-30, stage 31.11).

The federated round trip carries `next` in the `oidc_next` cookie and follows
it after signing the member in; the consumer's sign-out follows `?next=`
straight away. Both were open redirects. The round trip here is the real one
(bridge.py), so what is proved is what a browser would see.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ..bridge import FederationBrowser
from ..fixtures import federation_fixture, link_federated_identity

User = get_user_model()

PORTAL = "http://provider.test"
REDIRECT = "http://consumer.test/sso/callback/"
EVIL = "https://evil.example.com/phish"


class FederatedNextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Once per class: the signing key costs a real Argon2id derivation.
        federation_fixture(portal_url=PORTAL, redirect_uri=REDIRECT)

    def setUp(self):
        self.ada = User.objects.create_user("ada", "ada@example.org", "pw")
        link_federated_identity(self.ada)
        self.browser = FederationBrowser(portal_url=PORTAL)

    def test_a_local_next_survives_the_round_trip(self):
        response = self.browser.login(self.ada, next_url="/vault/")
        self.assertEqual(response["Location"], "/vault/")

    def test_an_off_site_next_never_reaches_the_cookie_and_lands_on_the_dashboard(self):
        self.browser.sign_in_at_provider(self.ada)
        started = self.browser.start_login(EVIL)
        self.assertNotIn("oidc_next", started.cookies)
        authorized = self.browser.authorize(started["Location"])
        response = self.browser.callback(authorized["Location"])
        self.assertEqual(response["Location"], reverse("core:dashboard"))
        self.assertEqual(int(self.browser.consumer.session["_auth_user_id"]), self.ada.pk)

    def test_a_forged_next_cookie_is_not_followed_either(self):
        self.browser.sign_in_at_provider(self.ada)
        started = self.browser.start_login()
        authorized = self.browser.authorize(started["Location"])
        self.browser.consumer.cookies["oidc_next"] = EVIL
        response = self.browser.callback(authorized["Location"])
        self.assertEqual(response["Location"], reverse("core:dashboard"))


class ConsumerSignOutTests(TestCase):
    def test_an_off_site_next_lands_on_the_welcome_page(self):
        response = self.client.post(reverse("sso:logout"), {"next": EVIL})
        self.assertEqual(response["Location"], reverse("core:welcome"))

    def test_a_local_next_is_followed(self):
        response = self.client.post(reverse("sso:logout"), {"next": "/core/login/"})
        self.assertEqual(response["Location"], "/core/login/")
