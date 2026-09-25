from urllib.parse import urlparse

from django.test import RequestFactory, SimpleTestCase, override_settings

from toto.api.fetch_metadata import cross_site_refusal
from toto.api.ws_origin import TotoOriginValidator


class OriginTests(SimpleTestCase):
    def setUp(self):
        self.validator = TotoOriginValidator(lambda *a: None)

    @override_settings(ALLOWED_HOSTS=["zenobia.example.org", ".example.net"])
    def test_allowed_hosts_are_accepted_and_others_refused(self):
        v = self.validator.valid_origin
        self.assertTrue(v(urlparse("https://zenobia.example.org")))
        self.assertTrue(v(urlparse("https://chat.example.net")))
        self.assertFalse(v(urlparse("https://evil.example.com")))
        self.assertFalse(v(urlparse("https://zenobia.example.org.evil.com")))

    def test_no_origin_and_the_desktop_scheme_are_accepted(self):
        self.assertTrue(self.validator.valid_origin(None))
        self.assertTrue(self.validator.valid_origin(urlparse("tauri://localhost")))


class FetchMetadataTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def test_a_cookie_write_from_another_site_is_refused(self):
        for site in ("same-site", "cross-site"):
            with self.subTest(site=site):
                request = self.rf.post("/x", HTTP_SEC_FETCH_SITE=site)
                self.assertEqual(cross_site_refusal(request).status_code, 403)

    def test_same_origin_reads_and_headerless_clients_pass(self):
        self.assertIsNone(cross_site_refusal(self.rf.post("/x", HTTP_SEC_FETCH_SITE="same-origin")))
        self.assertIsNone(cross_site_refusal(self.rf.post("/x")))
        self.assertIsNone(cross_site_refusal(self.rf.get("/x", HTTP_SEC_FETCH_SITE="cross-site")))

    def test_a_bearer_token_passes(self):
        request = self.rf.post("/x", HTTP_SEC_FETCH_SITE="cross-site")
        request._toto_bearer_auth = True
        self.assertIsNone(cross_site_refusal(request))
