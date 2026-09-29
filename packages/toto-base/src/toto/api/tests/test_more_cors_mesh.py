"""The API's doors, beyond the first suites (2026-09-29): which origins get
CORS, what a Bearer session key buys, the data-mesh gate's three answers, the
WebSocket token fallback and origin rule, and the desktop login endpoint's
refusals — each asserted on the view or middleware itself."""

import json
from urllib.parse import urlparse

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from toto.api.auth_views import HealthApiView, MeApiView
from toto.api.cors import (
    CORS_ALLOW_METHODS,
    DATA_MESH_GROUP,
    MeshGatedApiView,
    _is_allowed_origin,
    in_data_mesh,
    mesh_required,
    render_access_denied,
)
from toto.api.fetch_metadata import cross_site_refusal
from toto.api.middleware import TokenAuthMiddleware
from toto.api.models import _reject_secret_like_json
from toto.api.ws_origin import TotoOriginValidator, origin_of
from toto.audit.models import AuditRecord

User = get_user_model()


def session_key_for(user):
    store = SessionStore()
    store["_auth_user_id"] = str(user.pk)
    store.create()
    return store.session_key


class _Probe(MeshGatedApiView):
    """A gated resource, standing in for vault/events/locations/socialhub."""

    def get(self, request):
        return JsonResponse({"who": request.user.get_username()})

    def post(self, request):
        return JsonResponse({"written": True})


class OriginRuleTests(SimpleTestCase):
    def test_local_origins_on_any_port_and_the_desktop_scheme_are_allowed(self):
        for origin in ("http://localhost:1420", "http://127.0.0.1", "https://localhost",
                       "tauri://localhost", "tauri://anything"):
            with self.subTest(origin=origin):
                self.assertTrue(_is_allowed_origin(origin))

    def test_everything_else_is_refused_look_alikes_included(self):
        for origin in ("", "https://zenobia.example.org", "http://localhost.evil.com",
                       "http://127.0.0.1.nip.io", "http://evil.com/?localhost",
                       "null"):
            with self.subTest(origin=origin):
                self.assertFalse(_is_allowed_origin(origin))


class CorsHeaderTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def call(self, method, origin=""):
        extra = {"HTTP_ORIGIN": origin} if origin else {}
        request = getattr(self.rf, method)("/api/health/", **extra)
        request.user = AnonymousUser()
        return HealthApiView.as_view()(request)

    def test_a_preflight_from_the_desktop_gets_credentials_and_the_methods(self):
        response = self.call("options", "tauri://localhost")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "tauri://localhost")
        self.assertEqual(response["Access-Control-Allow-Credentials"], "true")
        self.assertEqual(response["Access-Control-Allow-Methods"], CORS_ALLOW_METHODS)

    def test_a_foreign_origin_is_never_echoed_back(self):
        response = self.call("get", "https://evil.example.com")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response)
        self.assertNotIn("Access-Control-Allow-Credentials", response)

    def test_an_ordinary_answer_carries_the_cors_headers_too(self):
        response = self.call("get", "http://localhost:5173")
        self.assertEqual(json.loads(response.content), {"ok": True, "service": "toto"})
        self.assertEqual(response["Access-Control-Allow-Origin"], "http://localhost:5173")


class BearerTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.ada = User.objects.create_user("ada", password="pw")

    def me(self, auth="", user=None):
        request = self.rf.get("/api/me/", **({"HTTP_AUTHORIZATION": auth} if auth else {}))
        request.user = user or AnonymousUser()
        return request, MeApiView.as_view()(request)

    def test_a_session_key_as_a_bearer_token_is_the_session_s_user(self):
        request, response = self.me(f"Bearer {session_key_for(self.ada)}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["username"], "ada")
        self.assertTrue(request._toto_bearer_auth)

    def test_an_unknown_key_a_blank_one_or_another_scheme_is_nobody(self):
        for auth in ("Bearer not-a-session", "Bearer ", "Basic YWRhOnB3",
                     f"bearer {session_key_for(self.ada)}"):
            with self.subTest(auth=auth):
                request, response = self.me(auth)
                self.assertEqual(response.status_code, 401)
                self.assertFalse(getattr(request, "_toto_bearer_auth", False))

    def test_a_session_that_signed_nobody_in_is_nobody(self):
        store = SessionStore()
        store["cart"] = "x"
        store.create()
        self.assertEqual(self.me(f"Bearer {store.session_key}")[1].status_code, 401)

    def test_the_session_of_a_deleted_account_is_nobody(self):
        key = session_key_for(self.ada)
        User.objects.filter(pk=self.ada.pk).delete()
        self.assertEqual(self.me(f"Bearer {key}")[1].status_code, 401)

    def test_a_cookie_user_is_kept_and_not_marked_as_a_token(self):
        bob = User.objects.create_user("bob", password="pw")
        request, response = self.me(f"Bearer {session_key_for(self.ada)}", user=bob)
        self.assertEqual(json.loads(response.content)["username"], "bob")
        self.assertFalse(getattr(request, "_toto_bearer_auth", False))

    def test_a_token_write_from_another_site_is_not_a_forgery(self):
        request = self.rf.post("/x", HTTP_SEC_FETCH_SITE="cross-site",
                               HTTP_AUTHORIZATION=f"Bearer {session_key_for(self.ada)}")
        request.user = AnonymousUser()
        from toto.api.cors import _try_bearer_auth

        _try_bearer_auth(request)
        self.assertIsNone(cross_site_refusal(request))


class MeshGateTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.ada = User.objects.create_user("ada", password="pw")
        self.group, _ = Group.objects.get_or_create(name=DATA_MESH_GROUP)

    def call(self, method="get", user=None, **extra):
        request = getattr(self.rf, method)("/gated/", **extra)
        request.user = user or AnonymousUser()
        return _Probe.as_view()(request)

    def test_nobody_signed_in_is_told_so_not_that_the_data_is_gated(self):
        response = self.call(HTTP_ORIGIN="http://localhost")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["Access-Control-Allow-Origin"], "http://localhost")

    def test_a_member_outside_the_mesh_is_told_to_pull_from_a_peer(self):
        response = self.call(user=self.ada)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(json.loads(response.content)["gated"])

    def test_a_mesh_member_reads_from_the_server(self):
        self.ada.groups.add(self.group)
        response = self.call(user=self.ada)
        self.assertEqual(json.loads(response.content), {"who": "ada"})

    def test_a_mesh_member_by_bearer_token_reads_too(self):
        self.ada.groups.add(self.group)
        response = self.call(HTTP_AUTHORIZATION=f"Bearer {session_key_for(self.ada)}")
        self.assertEqual(response.status_code, 200)

    def test_writes_are_not_behind_the_gate(self):
        response = self.call("post", user=self.ada)
        self.assertEqual(json.loads(response.content), {"written": True})

    def test_a_preflight_needs_no_account(self):
        self.assertEqual(self.call("options").status_code, 200)

    def test_membership_is_the_group_and_nothing_else(self):
        self.assertFalse(in_data_mesh(None))
        self.assertFalse(in_data_mesh(AnonymousUser()))
        self.assertFalse(in_data_mesh(User.objects.create_superuser("root", password="pw")))
        Group.objects.create(name="data_mesh_admins").user_set.add(self.ada)
        self.assertFalse(in_data_mesh(self.ada))
        self.ada.groups.add(self.group)
        self.assertTrue(in_data_mesh(self.ada))


class MeshPageTests(TestCase):
    def setUp(self):
        self.ada = User.objects.create_user("ada", password="pw")

    def page(self, user):
        request = RequestFactory().get("/gated-page/")
        request.user = user
        return mesh_required(lambda req: HttpResponse("the data"))(request)

    def test_a_page_outside_the_mesh_is_the_access_denied_page(self):
        response = self.page(self.ada)
        self.assertEqual(response.status_code, 403)
        self.assertIn(DATA_MESH_GROUP, response.content.decode())
        self.assertNotIn("the data", response.content.decode())

    def test_a_mesh_member_sees_the_page(self):
        self.ada.groups.add(Group.objects.get_or_create(name=DATA_MESH_GROUP)[0])
        self.assertEqual(self.page(self.ada).content, b"the data")

    def test_the_denied_page_can_carry_another_status(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        self.assertEqual(render_access_denied(request, status=401).status_code, 401)


class SocketTokenTests(TestCase):
    def setUp(self):
        self.ada = User.objects.create_user("ada", password="pw")

    def scope_after(self, scope):
        seen = {}

        async def inner(scope, receive, send):
            seen["user"] = scope.get("user")

        async_to_sync(TokenAuthMiddleware(inner))(scope, None, None)
        return seen["user"]

    def test_an_anonymous_socket_is_signed_in_by_its_token(self):
        scope = {"type": "websocket", "user": AnonymousUser(),
                 "query_string": f"token={session_key_for(self.ada)}".encode()}
        self.assertEqual(self.scope_after(scope), self.ada)

    def test_a_bad_token_leaves_the_socket_anonymous(self):
        scope = {"type": "websocket", "user": AnonymousUser(), "query_string": b"token=nope"}
        self.assertFalse(self.scope_after(scope).is_authenticated)

    def test_a_cookie_session_is_never_replaced_by_a_token(self):
        bob = User.objects.create_user("bob", password="pw")
        scope = {"type": "websocket", "user": bob,
                 "query_string": f"token={session_key_for(self.ada)}".encode()}
        self.assertEqual(self.scope_after(scope), bob)

    def test_only_sockets_read_the_token(self):
        scope = {"type": "http", "user": AnonymousUser(),
                 "query_string": f"token={session_key_for(self.ada)}".encode()}
        self.assertFalse(self.scope_after(scope).is_authenticated)

    def test_no_token_leaves_the_scope_as_it_was(self):
        scope = {"type": "websocket", "user": AnonymousUser(), "query_string": b"x=1"}
        self.assertIsInstance(self.scope_after(scope), AnonymousUser)


class SocketOriginTests(SimpleTestCase):
    def setUp(self):
        self.valid = TotoOriginValidator(lambda *a: None).valid_origin

    @override_settings(DEBUG=True, ALLOWED_HOSTS=[])
    def test_a_debug_host_with_no_allowed_hosts_accepts_only_loopback(self):
        self.assertTrue(self.valid(urlparse("http://localhost:8000")))
        self.assertTrue(self.valid(urlparse("http://127.0.0.1:8000")))
        self.assertFalse(self.valid(urlparse("https://evil.example.com")))

    @override_settings(DEBUG=False, ALLOWED_HOSTS=[])
    def test_a_production_host_with_no_allowed_hosts_accepts_no_browser(self):
        self.assertFalse(self.valid(urlparse("http://localhost:8000")))
        self.assertTrue(self.valid(None))

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_a_wildcard_host_accepts_any_origin(self):
        self.assertTrue(self.valid(urlparse("https://anywhere.example")))

    def test_the_origin_header_is_found_among_the_scope_headers(self):
        parsed = origin_of([(b"host", b"x"), (b"origin", b"https://zen.example.org")])
        self.assertEqual(parsed.hostname, "zen.example.org")
        self.assertIsNone(origin_of([(b"host", b"x")]))
        self.assertIsNone(origin_of(None))


class FetchMetadataEdgeTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def test_the_site_label_is_read_without_regard_to_case(self):
        refused = cross_site_refusal(self.rf.post("/x", HTTP_SEC_FETCH_SITE="Cross-Site"))
        self.assertEqual(refused.status_code, 403)

    def test_a_navigation_the_member_typed_is_not_foreign(self):
        self.assertIsNone(cross_site_refusal(self.rf.post("/x", HTTP_SEC_FETCH_SITE="none")))

    def test_head_and_options_are_never_refused(self):
        for method in ("head", "options"):
            request = getattr(self.rf, method)("/x", HTTP_SEC_FETCH_SITE="cross-site")
            self.assertIsNone(cross_site_refusal(request))

    def test_every_writing_method_is_checked(self):
        for method in ("put", "patch", "delete"):
            request = getattr(self.rf, method)("/x", HTTP_SEC_FETCH_SITE="same-site")
            self.assertEqual(cross_site_refusal(request).status_code, 403)


class DesktopLoginTests(TestCase):
    def setUp(self):
        self.ada = User.objects.create_user("ada", password="pass123")

    def post(self, body, path="/api/login/"):
        data = body if isinstance(body, str) else json.dumps(body)
        return self.client.post(path, data, content_type="application/json")

    def test_a_body_that_is_not_json_is_a_bad_request(self):
        response = self.post("{not json")
        self.assertEqual((response.status_code, response.json()),
                         (400, {"error": "Invalid JSON."}))

    def test_a_missing_or_blank_field_is_a_bad_request(self):
        for body in ({"username": "ada"}, {"password": "pass123"},
                     {"username": "   ", "password": "pass123"}):
            with self.subTest(body=body):
                self.assertEqual(self.post(body).status_code, 400)

    def me_by_token(self, token):
        # The view itself, as a host that lets a Bearer caller through reaches it.
        request = RequestFactory().get("/api/me/", HTTP_AUTHORIZATION=f"Bearer {token}")
        request.user = AnonymousUser()
        return MeApiView.as_view()(request)

    def test_the_token_it_hands_out_opens_the_api_as_that_account(self):
        token = self.post({"username": " ada ", "password": "pass123"}).json()["token"]
        self.assertEqual(json.loads(self.me_by_token(token).content)["username"], "ada")

    def test_a_sign_in_and_a_refusal_here_are_on_the_chain(self):
        self.post({"username": "ada", "password": "wrong"})
        self.post({"username": "ada", "password": "pass123"})
        refused = AuditRecord.objects.get(action="AUTH.LOGIN_FAILED")
        self.assertEqual(refused.request_source["path"], "/api/login/")
        self.assertEqual(AuditRecord.objects.get(action="AUTH.LOGIN").actor_user, self.ada)

    def test_signing_out_ends_the_token_s_session(self):
        token = self.post({"username": "ada", "password": "pass123"}).json()["token"]
        self.assertEqual(self.me_by_token(token).status_code, 200)
        self.assertEqual(self.client.post("/api/logout/").status_code, 200)
        self.assertEqual(self.me_by_token(token).status_code, 401)

    def test_the_profile_speaks_for_the_person_where_there_is_one(self):
        from toto.people.models import Person

        Person.objects.create(user=self.ada, display_name="Ada Lovelace")
        self.client.force_login(self.ada)
        body = self.client.get("/api/me/").json()
        self.assertEqual(body["full_name"], "Ada Lovelace")
        self.assertEqual(body["profile_url"], "/socialhub/profiles/ada-lovelace/")
        self.assertIsNone(body["avatar_url"])

    def test_a_language_patch_that_is_not_json_is_a_bad_request(self):
        self.client.force_login(self.ada)
        response = self.client.patch("/api/me/", "{", content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_the_mesh_answer_needs_an_account(self):
        self.assertEqual(self.client.get("/api/me/mesh/").status_code, 401)


class SecretLikeConfigTests(SimpleTestCase):
    def test_a_secret_named_anywhere_in_the_config_is_refused(self):
        from django.core.exceptions import ValidationError

        for value in ({"api_key": "x"}, {"nested": {"Client-Secret": "x"}},
                      {"list": [{"ok": 1}, {"TOKEN": "x"}]}, {"APIKEY": "x"}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                _reject_secret_like_json(value)

    def test_ordinary_config_passes(self):
        _reject_secret_like_json({"endpoint": "/v1/items", "params": {"page_size": 50},
                                  "records_path": ["data", "items"], "token_url": None})
