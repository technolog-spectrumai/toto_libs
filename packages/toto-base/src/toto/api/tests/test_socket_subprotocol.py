"""The WebSocket's token, out of the URL (2026-10-01).

The desktop client signed its socket in with ``?token=<session key>``, and a
URL is what logs keep: nginx's access log and uvicorn's own line for every
socket wrote the key down. A client now offers it in the subprotocol header,
after ``toto.bearer``; the server answers ``toto.bearer`` and never the key
(a browser closes a socket whose answer names none of the subprotocols it
offered), and the consumer behind sees the offer without the key. ``?token=``
still works for today's desktop clients, unchanged, and uvicorn's lines keep
the path without the query.
"""

import logging

from asgiref.sync import async_to_sync
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import Client, SimpleTestCase, TestCase, override_settings

from toto.api.middleware import BEARER_SUBPROTOCOL, TokenAuthMiddleware, bearer_offer
from toto.api.server_logs import UVICORN_LOGGERS, NoQueryString
from toto.audit.models import AuditRecord

User = get_user_model()


def session_key_for(user):
    """A real sign-in's key, backend and password hash included."""
    client = Client()
    client.force_login(user)
    return client.session.session_key


class _Recorder:
    """An inner application that accepts the socket and remembers its scope."""

    def __init__(self, subprotocol=None):
        self.subprotocol = subprotocol
        self.scope = None

    async def __call__(self, scope, receive, send):
        self.scope = scope
        await send({"type": "websocket.accept", "subprotocol": self.subprotocol})


class SocketCase(TestCase):
    def setUp(self):
        self.ada = User.objects.create_user("ada", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")

    def open(self, subprotocols=(), query=b"", user=None, inner=None):
        """The scope the inner app saw, and the messages sent back."""
        inner = inner or _Recorder()
        sent = []

        async def send(message):
            sent.append(message)

        scope = {"type": "websocket", "user": user or AnonymousUser(),
                 "query_string": query, "subprotocols": list(subprotocols),
                 "headers": [(b"host", b"zenobia.example.org")]}
        if subprotocols:
            scope["headers"].append((b"sec-websocket-protocol",
                                     ", ".join(subprotocols).encode()))
        async_to_sync(TokenAuthMiddleware(inner))(scope, None, send)
        return inner.scope, sent

    def answered(self, sent):
        accepts = [m for m in sent if m["type"] == "websocket.accept"]
        self.assertEqual(len(accepts), 1)
        return accepts[0].get("subprotocol")


class SubprotocolKeyTests(SocketCase):
    def test_the_key_after_toto_bearer_signs_the_socket_in(self):
        scope, sent = self.open([BEARER_SUBPROTOCOL, session_key_for(self.ada)])
        self.assertEqual(scope["user"], self.ada)
        self.assertEqual(self.answered(sent), BEARER_SUBPROTOCOL)

    def test_an_unknown_key_leaves_the_socket_anonymous(self):
        scope, sent = self.open([BEARER_SUBPROTOCOL, "x" * 32])
        self.assertFalse(scope["user"].is_authenticated)
        # Answered as a good key is: a refusal must not break the handshake
        # (nor tell the client why), and the consumer decides about anonymous.
        self.assertEqual(self.answered(sent), BEARER_SUBPROTOCOL)

    def test_a_deactivated_account_s_key_is_refused_and_recorded(self):
        key = session_key_for(self.ada)
        self.ada.is_active = False
        self.ada.save()
        scope, _sent = self.open([BEARER_SUBPROTOCOL, key])
        self.assertIsInstance(scope["user"], AnonymousUser)
        record = AuditRecord.objects.get(action="AUTH.TOKEN_REFUSED")
        self.assertEqual(record.metadata, {"door": "websocket", "reason": "inactive"})
        self.assertNotIn(key, str(record.metadata))

    def test_a_key_from_before_a_password_change_is_refused(self):
        key = session_key_for(self.ada)
        self.ada.set_password("Another-horse-7")
        self.ada.save()
        scope, _sent = self.open([BEARER_SUBPROTOCOL, key])
        self.assertFalse(scope["user"].is_authenticated)

    def test_toto_bearer_with_no_key_after_it_is_anonymous_but_answered(self):
        scope, sent = self.open([BEARER_SUBPROTOCOL])
        self.assertFalse(scope["user"].is_authenticated)
        self.assertEqual(self.answered(sent), BEARER_SUBPROTOCOL)

    def test_a_cookie_session_is_never_replaced_by_the_key(self):
        scope, sent = self.open([BEARER_SUBPROTOCOL, session_key_for(self.ada)],
                                user=self.bob)
        self.assertEqual(scope["user"], self.bob)
        self.assertEqual(self.answered(sent), BEARER_SUBPROTOCOL)

    def test_the_header_s_key_wins_over_the_query_s(self):
        query = f"token={session_key_for(self.ada)}".encode()
        scope, _sent = self.open([BEARER_SUBPROTOCOL, session_key_for(self.bob)],
                                 query=query)
        self.assertEqual(scope["user"], self.bob)

    def test_only_sockets_read_the_header(self):
        seen = {}

        async def app(scope, receive, send):
            seen.update(scope)

        scope = {"type": "http", "user": AnonymousUser(), "query_string": b"",
                 "subprotocols": [BEARER_SUBPROTOCOL, session_key_for(self.ada)]}
        async_to_sync(TokenAuthMiddleware(app))(scope, None, None)
        self.assertFalse(seen["user"].is_authenticated)


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class NoKeyInTheAnswerTests(SocketCase):
    def test_the_answer_never_carries_the_key(self):
        key = session_key_for(self.ada)
        _scope, sent = self.open([BEARER_SUBPROTOCOL, key])
        self.assertNotIn(key, repr(sent))

    def test_a_consumer_that_answers_the_key_is_corrected(self):
        key = session_key_for(self.ada)
        _scope, sent = self.open([BEARER_SUBPROTOCOL, key], inner=_Recorder(subprotocol=key))
        self.assertEqual(self.answered(sent), BEARER_SUBPROTOCOL)

    def test_a_consumer_s_own_choice_from_the_offer_is_kept(self):
        _scope, sent = self.open(["chat.v2", BEARER_SUBPROTOCOL, session_key_for(self.ada)],
                                 inner=_Recorder(subprotocol="chat.v2"))
        self.assertEqual(self.answered(sent), "chat.v2")

    def test_the_consumer_sees_the_offer_without_the_key(self):
        key = session_key_for(self.ada)
        scope, _sent = self.open(["chat.v2", BEARER_SUBPROTOCOL, key])
        self.assertEqual(scope["subprotocols"], ["chat.v2", BEARER_SUBPROTOCOL])
        self.assertEqual(dict(scope["headers"])[b"sec-websocket-protocol"],
                         b"chat.v2, toto.bearer")
        self.assertNotIn(key, repr(scope))

    def test_through_a_real_consumer_the_handshake_answers_toto_bearer(self):
        """Channels' own accept() names no subprotocol; the door supplies it."""

        class Members(AsyncWebsocketConsumer):
            async def connect(self):
                if self.scope["user"].is_authenticated:
                    await self.accept()
                else:
                    await self.close(code=4401)

        async def handshake(subprotocols):
            communicator = WebsocketCommunicator(TokenAuthMiddleware(Members.as_asgi()),
                                                 "/ws/members/", subprotocols=subprotocols)
            communicator.scope["user"] = AnonymousUser()
            answer = await communicator.connect()
            await communicator.disconnect()
            return answer

        key = session_key_for(self.ada)
        self.assertEqual(async_to_sync(handshake)([BEARER_SUBPROTOCOL, key]),
                         (True, BEARER_SUBPROTOCOL))
        self.assertEqual(async_to_sync(handshake)([BEARER_SUBPROTOCOL, "x" * 32]),
                         (False, 4401))


class QueryTokenTests(SocketCase):
    """Today's desktop clients: ``?token=`` works as it did."""

    def test_the_query_token_still_signs_the_socket_in(self):
        scope, sent = self.open(query=f"token={session_key_for(self.ada)}".encode())
        self.assertEqual(scope["user"], self.ada)
        # No subprotocol was offered, so none is answered: nothing changes
        # for a client that never asked for one.
        self.assertIsNone(self.answered(sent))

    def test_a_refused_query_token_leaves_the_socket_anonymous(self):
        scope, _sent = self.open(query=b"token=nope")
        self.assertFalse(scope["user"].is_authenticated)

    def test_an_offer_without_toto_bearer_is_left_alone(self):
        scope, sent = self.open(["chat.v2"], query=f"token={session_key_for(self.ada)}".encode())
        self.assertEqual(scope["user"], self.ada)
        self.assertEqual(scope["subprotocols"], ["chat.v2"])
        self.assertIsNone(self.answered(sent))


class BearerOfferTests(SimpleTestCase):
    def test_the_key_is_the_entry_after_toto_bearer(self):
        self.assertEqual(bearer_offer(["a", "toto.bearer", "k3y", "b"]),
                         ("k3y", ["a", "toto.bearer", "b"]))

    def test_no_toto_bearer_no_key(self):
        self.assertEqual(bearer_offer(["k3y"]), (None, ["k3y"]))
        self.assertEqual(bearer_offer(None), (None, []))

    def test_toto_bearer_last_names_no_key(self):
        self.assertEqual(bearer_offer(["toto.bearer"]), (None, ["toto.bearer"]))

    def test_empty_entries_are_no_key(self):
        self.assertEqual(bearer_offer(["toto.bearer", ""]), (None, ["toto.bearer"]))


class ServerLogTests(SimpleTestCase):
    """uvicorn's own lines keep the path without its query."""

    KEY = "s3ss10nk3yv4lu3s3ss10nk3yv4lu3xx"

    def setUp(self):
        TokenAuthMiddleware(lambda *args: None)

    def test_a_socket_line_keeps_no_token(self):
        with self.assertLogs("uvicorn.error", logging.INFO) as logs:
            logging.getLogger("uvicorn.error").info(
                '%s - "WebSocket %s" [accepted]', "10.0.0.4:51234",
                f"/ws/forum/hall/?token={self.KEY}")
        self.assertEqual(logs.output,
                         ['INFO:uvicorn.error:10.0.0.4:51234 - "WebSocket /ws/forum/hall/" [accepted]'])

    def test_an_http_line_keeps_no_query(self):
        with self.assertLogs("uvicorn.access", logging.INFO) as logs:
            logging.getLogger("uvicorn.access").info(
                '%s - "%s %s HTTP/%s" %d', "10.0.0.4:51234", "GET",
                "/account/email/confirm/?token=abc&x=1", "1.1", 302)
        self.assertEqual(logs.output,
                         ['INFO:uvicorn.access:10.0.0.4:51234 - "GET /account/email/confirm/ HTTP/1.1" 302'])

    def test_other_lines_are_left_as_they_are(self):
        with self.assertLogs("uvicorn.error", logging.INFO) as logs:
            logging.getLogger("uvicorn.error").info(
                "Uvicorn running on %s://%s:%d (Press CTRL+C to quit)", "http", "0.0.0.0", 8000)
        self.assertEqual(logs.output,
                         ["INFO:uvicorn.error:Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)"])

    def test_the_filter_is_put_on_once(self):
        TokenAuthMiddleware(lambda *args: None)
        for name in UVICORN_LOGGERS:
            with self.subTest(logger=name):
                filters = [f for f in logging.getLogger(name).filters
                           if isinstance(f, NoQueryString)]
                self.assertEqual(len(filters), 1)
