"""A token dies when the account or its password does (2026-09-30).

The desktop's token is the session key ``/api/login/`` hands out. Both doors
it opens — the JSON API's Bearer header and the WebSocket's ``?token=`` —
used to fetch the session's account with a bare lookup, so a deactivated
account and a changed password kept working for the session's fourteen days.
They go through ``toto.api.tokens`` now: the backend's ``get_user`` and the
session hash, as Django checks a cookie. A refused token is answered exactly
as no token, and the refusal goes on the chain without the key.
"""

import json
from unittest import mock

from asgiref.sync import async_to_sync
from django.contrib.auth import HASH_SESSION_KEY, get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory, TestCase, override_settings

from toto.api.auth_views import MeApiView
from toto.api.middleware import TokenAuthMiddleware
from toto.api.tokens import user_for_session_key
from toto.audit.models import AuditRecord

User = get_user_model()
PASSWORD = "Correct-horse-9"


class TokenCase(TestCase):
    def setUp(self):
        self.ada = User.objects.create_user("ada", password=PASSWORD)

    def token(self):
        """The key the desktop is handed, by the door it is handed at."""
        response = Client().post("/api/login/",
                                 json.dumps({"username": "ada", "password": PASSWORD}),
                                 content_type="application/json")
        self.assertEqual(response.status_code, 200)
        return response.json()["token"]

    def api(self, token=None):
        """``GET /api/me/`` on the view, as a JSON door reached by the desktop."""
        extra = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        request = RequestFactory().get("/api/me/", HTTP_ORIGIN="tauri://localhost", **extra)
        request.user = AnonymousUser()
        return request, MeApiView.as_view()(request)

    def socket(self, token=None):
        """The user a socket carries past the token middleware."""
        seen = {}

        async def inner(scope, receive, send):
            seen["user"] = scope["user"]

        query = f"token={token}".encode() if token else b""
        scope = {"type": "websocket", "user": AnonymousUser(), "query_string": query}
        async_to_sync(TokenAuthMiddleware(inner))(scope, None, None)
        return seen["user"]

    def deactivate(self):
        self.ada.is_active = False
        self.ada.save()

    def change_password(self):
        self.ada.set_password("Another-horse-7")
        self.ada.save()

    def refusals(self):
        return AuditRecord.objects.filter(action="AUTH.TOKEN_REFUSED")

    def session_exists(self, key):
        return SessionStore().exists(key)


class ValidTokenTests(TokenCase):
    def test_a_fresh_token_opens_the_json_door(self):
        request, response = self.api(self.token())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["username"], "ada")
        self.assertTrue(request._toto_bearer_auth)

    def test_a_fresh_token_signs_the_socket_in(self):
        self.assertEqual(self.socket(self.token()), self.ada)

    def test_a_token_is_used_again_and_again(self):
        token = self.token()
        for _ in range(3):
            self.assertEqual(self.api(token)[1].status_code, 200)
            self.assertEqual(self.socket(token), self.ada)
        self.assertFalse(self.refusals().exists())


class InactiveAccountTests(TokenCase):
    def test_a_deactivated_account_s_token_is_refused_at_the_json_door(self):
        token = self.token()
        self.deactivate()
        request, response = self.api(token)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(getattr(request, "_toto_bearer_auth", False))
        self.assertFalse(request.user.is_authenticated)

    def test_a_deactivated_account_s_token_leaves_the_socket_anonymous(self):
        token = self.token()
        self.deactivate()
        user = self.socket(token)
        self.assertIsInstance(user, AnonymousUser)
        self.assertFalse(user.is_authenticated)

    def test_the_refused_session_is_ended_and_stays_ended(self):
        token = self.token()
        self.deactivate()
        self.api(token)
        self.assertFalse(self.session_exists(token))
        # Re-activated, the account signs in again: the old token is gone.
        self.ada.is_active = True
        self.ada.save()
        self.assertEqual(self.api(token)[1].status_code, 401)

    def test_the_refusal_is_on_the_chain_once_with_the_door_and_the_reason(self):
        api_token, socket_token = self.token(), self.token()
        self.deactivate()
        for _ in range(3):
            self.api(api_token)
            self.socket(socket_token)
        records = list(self.refusals().order_by("sequence"))
        self.assertEqual([r.metadata for r in records],
                         [{"door": "api", "reason": "inactive"},
                          {"door": "websocket", "reason": "inactive"}])
        for record in records:
            self.assertFalse(record.success)
            self.assertIsNone(record.actor_user_id)
            self.assertEqual(record.object_id, str(self.ada.pk))
        self.assertEqual(records[0].request_source["path"], "/api/me/")


class PasswordChangeTests(TokenCase):
    def test_a_token_issued_before_a_password_change_is_refused_after_it(self):
        token = self.token()
        self.assertEqual(self.api(token)[1].status_code, 200)
        self.change_password()
        request, response = self.api(token)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(getattr(request, "_toto_bearer_auth", False))

    def test_the_socket_refuses_it_too(self):
        token = self.token()
        self.assertEqual(self.socket(token), self.ada)
        self.change_password()
        self.assertFalse(self.socket(token).is_authenticated)

    def test_the_stale_session_is_flushed_and_the_chain_says_why(self):
        token = self.token()
        self.change_password()
        self.socket(token)
        self.assertFalse(self.session_exists(token))
        self.assertEqual(self.refusals().get().metadata,
                         {"door": "websocket", "reason": "session_hash"})

    def test_a_token_issued_after_the_change_works(self):
        self.change_password()
        response = Client().post("/api/login/",
                                 json.dumps({"username": "ada",
                                             "password": "Another-horse-7"}),
                                 content_type="application/json")
        self.assertEqual(self.api(response.json()["token"])[1].status_code, 200)


class NoOracleTests(TokenCase):
    """Whatever is wrong with a token, the answer is the one for no token."""

    def answer(self, token=None):
        response = self.api(token)[1]
        return response.status_code, response.content, sorted(response.items())

    def test_every_refused_token_is_answered_as_no_token(self):
        nobody = self.answer()
        self.assertEqual(nobody[0], 401)
        inactive, stale, gone = self.token(), self.token(), self.token()
        self.deactivate()
        refused_inactive = self.answer(inactive)
        self.ada.is_active = True
        self.ada.save()
        self.change_password()
        refused_stale = self.answer(stale)
        self.ada.delete()
        for label, answer in (("inactive", refused_inactive),
                              ("password changed", refused_stale),
                              ("account deleted", self.answer(gone)),
                              ("unknown key", self.answer("x" * 32)),
                              ("flushed key", self.answer(stale))):
            with self.subTest(label):
                self.assertEqual(answer, nobody)

    def test_every_refused_socket_is_as_anonymous_as_no_token(self):
        inactive, stale = self.token(), self.token()
        self.deactivate()
        refused_inactive = self.socket(inactive)
        self.ada.is_active = True
        self.ada.save()
        self.change_password()
        for user in (refused_inactive, self.socket(stale), self.socket("x" * 32)):
            self.assertIs(type(user), type(self.socket()))
            self.assertFalse(user.is_authenticated)


class ChainTests(TokenCase):
    def test_the_key_is_never_on_the_chain(self):
        token = self.token()
        self.deactivate()
        self.api(token)
        for record in AuditRecord.objects.all():
            material = json.dumps([record.metadata, record.request_source, record.changes,
                                   record.object_id, record.object_description])
            self.assertNotIn(token, material)

    def test_an_unknown_key_writes_nothing(self):
        before = AuditRecord.objects.count()
        self.api("x" * 32)
        self.socket("y" * 32)
        self.assertEqual(AuditRecord.objects.count(), before)

    def test_a_deleted_account_is_named_by_its_id(self):
        token = self.token()
        pk = self.ada.pk
        self.ada.delete()
        self.api(token)
        self.assertEqual(self.refusals().get().metadata,
                         {"door": "api", "reason": "no_account", "account_id": str(pk)})


class CheckTests(TokenCase):
    """The helper itself, on the edges the doors do not reach."""

    def test_a_backend_no_longer_trusted_refuses_the_sign_in(self):
        token = self.token()
        elsewhere = f"{__name__}.Elsewhere"
        with override_settings(AUTHENTICATION_BACKENDS=[elsewhere]):
            self.assertIsNone(user_for_session_key(token, door="api"))
        self.assertEqual(self.refusals().get().metadata["reason"], "backend")

    def test_a_rotated_secret_keeps_the_token_and_its_key(self):
        token = self.token()
        with override_settings(SECRET_KEY="a-new-secret-" + "k" * 40,
                               SECRET_KEY_FALLBACKS=[self._secret()]):
            self.assertEqual(user_for_session_key(token, door="api"), self.ada)
            # Re-signed in place: the key the desktop holds is still the key.
            self.assertTrue(self.session_exists(token))
            store = SessionStore(session_key=token)
            self.assertEqual(store[HASH_SESSION_KEY], self.ada.get_session_auth_hash())
        self.assertFalse(self.refusals().exists())

    def test_a_passing_fault_is_not_a_refusal(self):
        token = self.token()
        with mock.patch("django.contrib.auth.load_backend", side_effect=RuntimeError("db")):
            self.assertIsNone(user_for_session_key(token, door="api"))
        self.assertTrue(self.session_exists(token))
        self.assertFalse(self.refusals().exists())
        self.assertEqual(user_for_session_key(token, door="api"), self.ada)

    def test_nothing_that_is_not_a_key_is_looked_up(self):
        for value in (None, "", 42, b"bytes"):
            with self.subTest(value=value):
                self.assertIsNone(user_for_session_key(value, door="api"))

    @staticmethod
    def _secret():
        from django.conf import settings

        return settings.SECRET_KEY


class Elsewhere:
    """A backend the host lists instead of the one the token was signed with."""

    def authenticate(self, request, **credentials):
        return None

    def get_user(self, user_id):
        return None
