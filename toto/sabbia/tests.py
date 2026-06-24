import json
from unittest import mock

from asgiref.sync import async_to_sync
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, TransactionTestCase, override_settings

from toto.gervazy.crypto import GervazyCryptoSession
from toto.sabbia import vault
from toto.sabbia.endpoints import REGISTRY, get_endpoint
from toto.sabbia.endpoints.openai import OpenAIChatEndpoint
from toto.sabbia.models import Agent, AgentConnector
from toto.sabbia.routing import websocket_urlpatterns

User = get_user_model()

VAULT_PW = "test-vault-pw"


class EndpointRegistryTests(TestCase):
    def test_known_types_resolve(self):
        agent = Agent(name="X", endpoint_type=Agent.ENDPOINT_OPENAI)
        self.assertIsInstance(get_endpoint(agent), OpenAIChatEndpoint)
        self.assertIn("openai", REGISTRY)
        self.assertIn("ollama", REGISTRY)

    def test_unknown_type_raises(self):
        agent = Agent(name="Y", endpoint_type="nope")
        with self.assertRaises(RuntimeError):
            get_endpoint(agent)


@override_settings(SABBIA_VAULT_PASSWORD=VAULT_PW)
class VaultTests(TestCase):
    def setUp(self):
        vault.clear_cache()
        owner = User.objects.create(username=vault.SYSTEM_OWNER_USERNAME, is_active=False)
        GervazyCryptoSession.initialize_strongbox(
            owner, vault.SYSTEM_STRONGBOX_NAME, VAULT_PW
        )
        vault.clear_cache()

    def tearDown(self):
        vault.clear_cache()

    def test_store_and_decrypt_via_connector(self):
        secret = vault.store_secret("sk-secret-123", name="t", purpose="test")
        connector = AgentConnector.objects.create(
            name="C", provider=AgentConnector.PROVIDER_OPENAI, api_secret=secret
        )
        recovered = connector.decrypt_api_secret(vault_session=vault.open_session())
        self.assertEqual(recovered, "sk-secret-123")


class VicunaChatViewTests(TestCase):
    """The vicuna proxy view (only present when toto.vicuna is installed)."""

    def setUp(self):
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.vicuna"):
            self.skipTest("toto.vicuna not installed")

    def _post(self, body, **extra):
        from django.urls import reverse

        return self.client.post(
            reverse("vicuna:chat"),
            data=json.dumps(body),
            content_type="application/json",
            **extra,
        )

    def test_chat_proxies_to_ollama(self):
        fake = mock.MagicMock()
        fake.read.return_value = json.dumps(
            {"message": {"content": "hello back"}}
        ).encode()
        fake.__enter__.return_value = fake
        fake.__exit__.return_value = False
        with mock.patch("toto.vicuna.views.urlopen", return_value=fake):
            resp = self._post({"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["reply"], "hello back")

    def test_missing_messages_is_bad_request(self):
        resp = self._post({})
        self.assertEqual(resp.status_code, 400)

    @override_settings(VICUNA_INTERNAL_TOKEN="sekret")
    def test_token_guard(self):
        resp = self._post({"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(resp.status_code, 403)


@override_settings(SABBIA_VAULT_PASSWORD=VAULT_PW)
class AgentChatConsumerTests(TransactionTestCase):
    def _make_agent(self):
        return Agent.objects.create(
            name="Steven", slug="steven", endpoint_type=Agent.ENDPOINT_OPENAI,
            model_name="gpt-4.1-mini", is_active=True,
        )

    def _communicator(self, user):
        app = URLRouter(websocket_urlpatterns)
        communicator = WebsocketCommunicator(app, "/ws/sabbia/agent/steven/")
        communicator.scope["user"] = user
        return communicator

    def test_anonymous_rejected(self):
        self._make_agent()

        async def run():
            communicator = self._communicator(AnonymousUser())
            connected, _ = await communicator.connect()
            await communicator.disconnect()
            return connected

        self.assertFalse(async_to_sync(run)())

    def test_happy_path_single_shot(self):
        self._make_agent()
        user = User.objects.create(username="u1")

        async def run():
            communicator = self._communicator(user)
            with mock.patch.object(OpenAIChatEndpoint, "chat", return_value="pong"):
                connected, _ = await communicator.connect()
                assert connected
                ack = await communicator.receive_json_from()
                await communicator.send_json_to({"type": "user_message", "content": "ping"})
                # typing(true), typing(false), assistant_message — collect until reply.
                reply = None
                for _ in range(5):
                    msg = await communicator.receive_json_from()
                    if msg.get("type") == "assistant_message":
                        reply = msg
                        break
                await communicator.disconnect()
                return ack, reply

        ack, reply = async_to_sync(run)()
        self.assertEqual(ack["type"], "ack")
        self.assertEqual(reply["content"], "pong")
