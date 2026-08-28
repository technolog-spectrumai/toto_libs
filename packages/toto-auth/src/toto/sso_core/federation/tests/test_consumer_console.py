"""The child's federation console (`sso_client`): show the parent, redeem a code, test.

Runs under the consumer-shaped urlconf, where the `sso` namespace resolves to sso_client,
so `sso:federation_console` reverses to `/sso/federation/` — the same name the provider
serves, which is what lets one dashboard card link either host.
"""
from unittest import mock

import requests as http_requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform
from toto.sso_client import parent_info
from toto.sso_client.models import OIDCProviderConfig

from ..fixtures import federation_fixture

User = get_user_model()

PORTAL = "https://provider.test"
REDIRECT = "https://consumer.test/sso/callback/"
CONSUMER_URLS = "toto.sso_core.federation.consumer_urls"


@override_settings(ROOT_URLCONF=CONSUMER_URLS)
class ConsumerConsoleGateAndRedeemTests(TestCase):
    """No parent configured yet: the gate, and the redeem form."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Studio", author="x", publication_year=2026, active=True)
        cls.staff = User.objects.create_user("boss", "boss@x.test", "pw", is_staff=True)
        cls.plain = User.objects.create_user("joe", "joe@x.test", "pw")

    def setUp(self):
        self.url = reverse("sso:federation_console")

    def test_anonymous_is_sent_to_login(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 302)
        self.assertIn("login", res["Location"])

    def test_a_non_staff_user_is_refused(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_unpaired_shows_the_redeem_form_directly(self):
        self.client.force_login(self.staff)
        body = self.client.get(self.url).content.decode()
        self.assertIn("Redeem and federate", body)
        self.assertNotIn("Federated with", body)      # not paired
        self.assertNotIn("Enter a new code", body)     # the form is up front, not behind a toggle

    def test_redeeming_a_code_drives_pair_and_redirects(self):
        self.client.force_login(self.staff)
        fake = mock.Mock(label="Zenobia")
        with mock.patch("toto.sso_client.pairing.pair", return_value=fake) as pair:
            res = self.client.post(self.url, {
                "action": "redeem", "target": PORTAL,
                "code": "A-PAIRING-CODE", "label": "Zenobia",
            })
        self.assertTrue(pair.called)
        self.assertEqual(pair.call_args.kwargs["expect_url"], PORTAL)
        self.assertRedirects(res, self.url, fetch_redirect_response=False)

    def test_a_blank_code_is_a_form_error_not_a_pair_call(self):
        self.client.force_login(self.staff)
        with mock.patch("toto.sso_client.pairing.pair") as pair:
            res = self.client.post(self.url, {"action": "redeem", "target": PORTAL, "code": ""})
        self.assertFalse(pair.called)
        self.assertEqual(res.status_code, 200)         # re-rendered with the error
        self.assertContains(res, "Paste the pairing code")


@override_settings(ROOT_URLCONF=CONSUMER_URLS)
class ConsumerConsolePairedTests(TestCase):
    """A parent is configured and paired: show it, hide the code box, offer Test."""

    @classmethod
    def setUpTestData(cls):
        federation_fixture(portal_url=PORTAL, redirect_uri=REDIRECT)
        # The fixture leaves paired_at unset; stamp it so the console reads "paired".
        OIDCProviderConfig.objects.filter(active=True).update(paired_at=timezone.now())
        cls.staff = User.objects.create_user("boss", "boss@x.test", "pw", is_staff=True)

    def setUp(self):
        self.url = reverse("sso:federation_console")
        self.client.force_login(self.staff)

    def test_paired_shows_parent_info_and_tucks_the_code_box_away(self):
        info = {"site_name": "Zenobia", "domain": "zenobia.test", "logo_url": None, "federation": None}
        with mock.patch("toto.sso_client.parent_info.fetch_platform_info", return_value=info):
            body = self.client.get(self.url).content.decode()
        self.assertIn("Federated with", body)
        self.assertIn("Zenobia", body)
        self.assertIn("Test the connection", body)
        self.assertIn("Enter a new code", body)        # re-pair is behind this control

    def test_the_joined_federation_is_named(self):
        """Joining is joining a FEDERATION — the console says which one.

        `platform_info` has always carried the parent's federation; this page
        used to drop it, showing only which PLATFORM it paired with.
        """
        info = {"site_name": "Zenobia", "domain": "zenobia.test",
                "logo_url": None,
                "federation": {"name": "The Holding", "logo_url": None}}
        with mock.patch("toto.sso_client.parent_info.fetch_platform_info",
                        return_value=info):
            body = self.client.get(self.url).content.decode()
        self.assertIn("The Holding", body)
        self.assertIn("Part of", body)

    def test_a_parent_without_a_federation_says_nothing_about_one(self):
        """The common case: most platforms belong to no federation."""
        info = {"site_name": "Zenobia", "domain": "zenobia.test",
                "logo_url": None, "federation": None}
        with mock.patch("toto.sso_client.parent_info.fetch_platform_info",
                        return_value=info):
            body = self.client.get(self.url).content.decode()
        self.assertIn("Zenobia", body)
        self.assertNotIn("Part of", body)

    def test_falls_back_to_local_config_when_the_parent_is_unreachable(self):
        with mock.patch("toto.sso_client.parent_info.fetch_platform_info", return_value=None):
            body = self.client.get(self.url).content.decode()
        # The stored label still shows, with an honest "could not be reached" note.
        self.assertIn("Federation suite", body)
        self.assertIn("could not be reached", body)

    def test_the_test_button_reports_ok(self):
        with mock.patch("toto.sso_client.parent_info.fetch_platform_info",
                        return_value={"site_name": "Zenobia"}):
            res = self.client.post(self.url, {"action": "test"})
        self.assertEqual(res.status_code, 302)          # PRG back to the console

    def test_the_test_button_reports_failure(self):
        with mock.patch("toto.sso_client.parent_info.fetch_platform_info", return_value=None):
            res = self.client.post(self.url, {"action": "test"}, follow=True)
        self.assertContains(res, "Could not reach the parent")


class ParentInfoClientTests(TestCase):
    """The platform-info client — a soft edge that never raises."""

    def _cfg(self, **kw):
        base = {"portal_url": PORTAL, "client_id": "studio", "client_secret": "s3cret"}
        base.update(kw)
        return base

    def test_none_without_credentials(self):
        self.assertIsNone(parent_info.fetch_platform_info(self._cfg(client_secret="")))

    def test_returns_the_json_on_200(self):
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"site_name": "Zenobia"}
        with mock.patch("toto.sso_client.views.http_requests.get", return_value=resp):
            got = parent_info.fetch_platform_info(self._cfg())
        self.assertEqual(got["site_name"], "Zenobia")

    def test_none_on_a_rejected_credential(self):
        resp = mock.Mock(status_code=401)
        with mock.patch("toto.sso_client.views.http_requests.get", return_value=resp):
            self.assertIsNone(parent_info.fetch_platform_info(self._cfg()))

    def test_none_when_unreachable(self):
        with mock.patch("toto.sso_client.views.http_requests.get",
                        side_effect=http_requests.ConnectTimeout("nope")):
            self.assertIsNone(parent_info.fetch_platform_info(self._cfg()))
