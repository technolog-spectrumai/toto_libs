from urllib.parse import urlparse

from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

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


class OriginFallbackTests(SimpleTestCase):
    """A browser too old for Fetch Metadata still sends Origin on a POST."""

    def setUp(self):
        self.rf = RequestFactory()

    def test_a_foreign_origin_without_the_site_label_is_refused(self):
        for origin in ("https://evil.example", "http://testserver.evil.example", "null"):
            with self.subTest(origin=origin):
                request = self.rf.post("/x", HTTP_ORIGIN=origin)
                self.assertEqual(cross_site_refusal(request).status_code, 403)

    def test_the_platforms_own_origin_passes(self):
        self.assertIsNone(cross_site_refusal(
            self.rf.post("/x", HTTP_ORIGIN="https://testserver")))

    @override_settings(CORS_ALLOWED_ORIGINS=["tauri://localhost"])
    def test_the_desktop_apps_named_origin_passes_even_labelled_cross_site(self):
        request = self.rf.post("/x", HTTP_ORIGIN="tauri://localhost",
                               HTTP_SEC_FETCH_SITE="cross-site")
        self.assertIsNone(cross_site_refusal(request))
        request = self.rf.post("/x", HTTP_ORIGIN="http://localhost:31337",
                               HTTP_SEC_FETCH_SITE="same-site")
        self.assertEqual(cross_site_refusal(request).status_code, 403)


@override_settings(VAULT_STORAGE_ONLY=False)
class SessionDoorTests(TestCase):
    """Stage 51 (zenobia/todo.md item 2): the cookie-authenticated
    `csrf_exempt` write doors answered a forged same-site text/plain POST
    with 201. Every CorsApiView, and the vault's create-file door, now asks
    the Fetch-Metadata guard first; a Bearer token still passes.

    `VAULT_STORAGE_ONLY` is set off for the class: the vault's create-file
    door is there only on a host that makes files, and the guard in front of
    it can only be asked where the door is. The last test says what a host
    that sets the flag answers (zenobia, 2026-10-03)."""

    DOORS = (
        "/vault/api/directories/",
        "/vault/api/files/create/",
        "/events/api/enigma/list/",
        "/assets/api/wallet/pin/verify/",
        "/bourse/api/enigma/proposals/create/",
        "/vault/file/create/",
    )
    REFUSED = "Cross-site request refused."

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model

        cls.ada = get_user_model().objects.create_user("ada", password="pw")

    def _doors(self):
        from django.urls import Resolver404, resolve

        for path in self.DOORS:
            try:
                resolve(path)
            except Resolver404:
                continue
            yield path

    def _forged(self, client, path, **extra):
        return client.post(path, data='{"name": "planted", "title": "x"}',
                           content_type="text/plain", HTTP_SEC_FETCH_SITE="same-site",
                           **extra)

    def _is_refusal(self, response):
        return (response.status_code == 403
                and response.get("Content-Type", "").startswith("application/json")
                and response.json().get("error") == self.REFUSED)

    def test_a_forged_cookie_write_is_refused_at_every_door(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.ada)
        checked = 0
        for path in self._doors():
            checked += 1
            with self.subTest(path=path):
                self.assertTrue(self._is_refusal(self._forged(client, path)))
        self.assertTrue(checked)

    def test_a_forged_desktop_sign_in_sets_no_session(self):
        response = Client(enforce_csrf_checks=True).post(
            "/api/login/", data='{"username": "ada", "password": "pw"}',
            content_type="text/plain", HTTP_SEC_FETCH_SITE="cross-site")
        self.assertTrue(self._is_refusal(response))
        self.assertNotIn("sessionid", response.cookies)

    def test_the_same_write_with_a_bearer_token_is_not_refused(self):
        key_client = Client()
        key_client.force_login(self.ada)
        key = key_client.session.session_key
        for path in self._doors():
            if path == "/vault/file/create/":
                continue                # a browser form door: no token there
            with self.subTest(path=path):
                response = self._forged(Client(enforce_csrf_checks=True), path,
                                        HTTP_AUTHORIZATION=f"Bearer {key}")
                self.assertFalse(self._is_refusal(response))

    @override_settings(VAULT_STORAGE_ONLY=True)
    def test_a_storage_only_host_has_no_create_file_door_to_forge_at(self):
        """There the door answers 404 to everybody, a forged write included,
        and nothing is planted; the other doors are still there and still
        refuse the forgery."""
        door = "/vault/file/create/"
        doors = list(self._doors())
        if door not in doors:
            self.skipTest("the vault's create-file door is not mounted on this host")
        from toto.vault.models import VaultDirectory, VaultFile

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.ada)
        self.assertEqual(self._forged(client, door).status_code, 404)
        for path in doors:
            if path == door:
                continue
            with self.subTest(path=path):
                self.assertTrue(self._is_refusal(self._forged(client, path)))
        self.assertFalse(VaultFile.objects.exists())
        self.assertFalse(VaultDirectory.objects.filter(name="planted").exists())
