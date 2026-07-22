import json
import os
from unittest import mock, skipUnless

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


def _init_system_vault():
    vault.clear_cache()
    owner = User.objects.create(username=vault.SYSTEM_OWNER_USERNAME, is_active=False)
    GervazyCryptoSession.initialize_strongbox(owner, vault.SYSTEM_STRONGBOX_NAME, VAULT_PW)
    vault.clear_cache()


@override_settings(SABBIA_VAULT_PASSWORD=VAULT_PW)
class VaultRotationTests(TestCase):
    def setUp(self):
        _init_system_vault()

    def tearDown(self):
        vault.clear_cache()

    def test_reencrypt_preserves_value_under_new_key(self):
        s1 = vault.store_secret("sk-abc", name="x-1", purpose="p")
        s2 = vault.reencrypt_secret(s1, name="x-2")
        self.assertNotEqual(s2.wrapped_key_id, s1.wrapped_key_id)  # fresh data key
        self.assertEqual(vault.open_session().decrypt_secret(s2), "sk-abc")  # same value

    def test_retire_secret_retires_unused_dek(self):
        s1 = vault.store_secret("v1", name="r-1")
        s2 = vault.reencrypt_secret(s1, name="r-2")  # s2 on a dedicated fresh DEK
        dek2 = s2.wrapped_key
        vault.retire_secret(s2)
        s2.refresh_from_db()
        dek2.refresh_from_db()
        self.assertEqual(s2.state, "retired")
        self.assertEqual(dek2.state, "retired")  # no other active secret used it

    def test_retire_keeps_shared_dek(self):
        s1 = vault.store_secret("v1", name="s-1")
        s2 = vault.store_secret("v2", name="s-2")
        self.assertEqual(s1.wrapped_key_id, s2.wrapped_key_id)  # share the active DEK
        shared = s1.wrapped_key
        vault.retire_secret(s1)
        shared.refresh_from_db()
        self.assertEqual(shared.state, "active")  # s2 still uses it → not retired

    def test_log_secret_event(self):
        from toto.gervazy.models import CryptoAuditLog

        s = vault.store_secret("v", name="l-1")
        actor = User.objects.create(username="auditor")
        vault.log_secret_event(actor, "set_connector_secret", s, reason="set via admin")
        log = CryptoAuditLog.objects.get(action="set_connector_secret")
        self.assertEqual(log.object_id, str(s.pk))
        self.assertTrue(log.success)


@override_settings(SABBIA_VAULT_PASSWORD=VAULT_PW)
class SecretAdminTests(TestCase):
    def setUp(self):
        _init_system_vault()
        self.admin = User.objects.create_superuser("root", "r@example.com", "pw")
        self.client.force_login(self.admin)

    def tearDown(self):
        vault.clear_cache()

    def _save_model(self, conn, value):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import RequestFactory

        from toto.sabbia.admin import AgentConnectorAdmin

        ma = AgentConnectorAdmin(AgentConnector, AdminSite())
        req = RequestFactory().post("/")
        req.user = self.admin
        req.session = SessionStore()
        req.session.create()
        req._messages = FallbackStorage(req)
        form = mock.Mock()
        form.cleaned_data = {"new_secret_value": value}
        ma.save_model(req, conn, form, change=bool(conn.pk))
        return req

    def test_set_value_stores_encrypted_and_audits(self):
        from toto.gervazy.models import CryptoAuditLog

        conn = AgentConnector(name="C", provider=AgentConnector.PROVIDER_OPENAI)
        self._save_model(conn, "sk-set-1")
        conn.refresh_from_db()
        self.assertIsNotNone(conn.api_secret_id)
        self.assertEqual(conn.decrypt_api_secret(vault_session=vault.open_session()), "sk-set-1")
        self.assertTrue(CryptoAuditLog.objects.filter(action="set_connector_secret").exists())

    def test_set_value_replaces_and_retires_old(self):
        old = vault.store_secret("sk-old", name="c-old")
        conn = AgentConnector.objects.create(
            name="C", provider=AgentConnector.PROVIDER_OPENAI, api_secret=old
        )
        self._save_model(conn, "sk-new")
        conn.refresh_from_db()
        self.assertNotEqual(conn.api_secret_id, old.id)
        self.assertEqual(conn.decrypt_api_secret(vault_session=vault.open_session()), "sk-new")
        old.refresh_from_db()
        self.assertEqual(old.state, "retired")

    def test_blank_value_keeps_secret(self):
        s = vault.store_secret("sk-keep", name="c-keep")
        conn = AgentConnector.objects.create(
            name="C", provider=AgentConnector.PROVIDER_OPENAI, api_secret=s
        )
        self._save_model(conn, "")
        conn.refresh_from_db()
        self.assertEqual(conn.api_secret_id, s.id)  # unchanged

    def test_vault_unavailable_is_surfaced(self):
        conn = AgentConnector(name="C", provider=AgentConnector.PROVIDER_OPENAI)
        with mock.patch("toto.sabbia.vault.store_secret", side_effect=vault.VaultUnavailable("nope")):
            req = self._save_model(conn, "sk-x")
        conn.refresh_from_db()
        self.assertIsNone(conn.api_secret_id)  # not changed
        self.assertTrue(any("Vault unavailable" in m.message for m in req._messages))

    def test_reencrypt_action_rotates_key(self):
        from django.urls import reverse

        from toto.gervazy.models import CryptoAuditLog

        s = vault.store_secret("sk-rot", name="c-rot")
        conn = AgentConnector.objects.create(
            name="C", provider=AgentConnector.PROVIDER_OPENAI, api_secret=s
        )
        self.client.post(
            reverse("admin:sabbia_agentconnector_changelist"),
            {"action": "reencrypt_api_secret", "_selected_action": [str(conn.pk)]},
        )
        conn.refresh_from_db()
        self.assertNotEqual(conn.api_secret_id, s.id)
        self.assertNotEqual(conn.api_secret.wrapped_key_id, s.wrapped_key_id)
        self.assertEqual(conn.decrypt_api_secret(vault_session=vault.open_session()), "sk-rot")
        s.refresh_from_db()
        self.assertEqual(s.state, "retired")
        self.assertTrue(CryptoAuditLog.objects.filter(action="reencrypt_connector_secret").exists())

    def test_change_form_is_write_only(self):
        from django.urls import reverse

        s = vault.store_secret("sk-PLAINTEXT-XYZ", name="c-wo")
        conn = AgentConnector.objects.create(
            name="WO", provider=AgentConnector.PROVIDER_OPENAI, api_secret=s
        )
        res = self.client.get(reverse("admin:sabbia_agentconnector_change", args=[conn.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Set / replace secret value")
        self.assertNotContains(res, "sk-PLAINTEXT-XYZ")  # plaintext never rendered


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


@override_settings(
    SABBIA_OPENAI=True,
    SABBIA_VAULT_PASSWORD=VAULT_PW,
    OPENAI_API_KEY="sk-test-dummy-key",
)
class IngressVaultBootstrapTests(TestCase):
    """ingress_sabbia must initialize the credential vault itself and store the
    OPENAI_API_KEY — without a separate manual `sabbia_init_vault` step (the bug
    that left the connector's api_secret empty and Steven keyless)."""

    def setUp(self):
        from toto.core.models import Platform

        vault.clear_cache()
        Platform.objects.create(
            site_name="Test", author="T", publication_year=2024, active=True
        )

    def tearDown(self):
        vault.clear_cache()

    def test_ingress_initializes_vault_and_wires_key(self):
        from django.core.management import call_command

        # Pre-condition: no strongbox — the state in which the old code silently
        # failed to store the key (and Steven ran keyless).
        self.assertIsNone(vault.system_strongbox())

        call_command("ingress_sabbia", verbosity=0)

        # Vault created…
        self.assertIsNotNone(vault.system_strongbox())
        # …key stored and wired to the connector…
        connector = AgentConnector.objects.get(slug="sabbia-openai")
        self.assertIsNotNone(connector.api_secret_id)
        recovered = connector.decrypt_api_secret(vault_session=vault.open_session())
        self.assertEqual(recovered, "sk-test-dummy-key")
        # …and Steven is active and points at that connector.
        steven = Agent.objects.get(slug="steven")
        self.assertTrue(steven.is_active)
        self.assertEqual(steven.connector_id, connector.id)


@skipUnless(
    os.environ.get("OPENAI_API_KEY", "").strip(),
    "live test — set OPENAI_API_KEY in the env to verify OpenAI actually responds",
)
class OpenAILiveResponseTests(TestCase):
    """End-to-end LIVE check: with the key in the env, the full pipeline
    (vault → connector → endpoint) reaches OpenAI and returns a non-empty reply.
    Skipped unless OPENAI_API_KEY is set, so normal CI never makes a paid call."""

    def setUp(self):
        from toto.core.models import Platform

        vault.clear_cache()
        Platform.objects.create(
            site_name="Test", author="T", publication_year=2024, active=True
        )

    def tearDown(self):
        vault.clear_cache()

    def test_openai_responds_with_env_key(self):
        from django.core.management import call_command

        key = os.environ["OPENAI_API_KEY"].strip()
        with override_settings(
            SABBIA_OPENAI=True, SABBIA_VAULT_PASSWORD=VAULT_PW, OPENAI_API_KEY=key
        ):
            call_command("ingress_sabbia", verbosity=0)
            agent = Agent.objects.get(slug="steven")
            reply = OpenAIChatEndpoint(agent).chat(
                [{"role": "user", "content": "Reply with exactly one word: pong"}]
            )

        self.assertIsInstance(reply, str)
        self.assertTrue(reply.strip(), "OpenAI returned an empty reply")


class GraphRAGRoutingTests(TestCase):
    """The consumer's RAG routing/fallback logic, exercised directly (no
    websocket layer / channel backend needed)."""

    def _consumer(self, endpoint_config):
        from toto.sabbia.consumers import AgentChatConsumer

        c = AgentChatConsumer.__new__(AgentChatConsumer)  # bypass channels __init__
        c.agent = Agent(
            name="A", slug="a", endpoint_type=Agent.ENDPOINT_OPENAI,
            model_name="gpt-4.1-mini", endpoint_config=endpoint_config, is_active=True,
        )
        return c

    def test_graphrag_answer_returns_rag_reply(self):
        c = self._consumer({"rag": {"enabled": True}})
        with mock.patch("toto.sabbia.graphrag_llm.build_llm", return_value="LLM"), \
             mock.patch("toto.ravioli.rag.run_graphrag", return_value=("rag answer", "ctx")) as rg:
            out = c._graphrag_answer("hi", {"top_k": 3, "text2cypher": True})
        self.assertEqual(out, "rag answer")
        rg.assert_called_once_with(llm="LLM", query_text="hi", top_k=3, text2cypher=True)

    def test_graphrag_answer_none_when_run_returns_empty(self):
        c = self._consumer({"rag": {"enabled": True}})
        with mock.patch("toto.sabbia.graphrag_llm.build_llm", return_value="LLM"), \
             mock.patch("toto.ravioli.rag.run_graphrag", return_value=("", "")):
            self.assertIsNone(c._graphrag_answer("hi", {}))

    def test_graphrag_answer_none_when_no_llm(self):
        c = self._consumer({"rag": {"enabled": True}})
        with mock.patch("toto.sabbia.graphrag_llm.build_llm", return_value=None), \
             mock.patch("toto.ravioli.rag.run_graphrag") as rg:
            self.assertIsNone(c._graphrag_answer("hi", {}))
        rg.assert_not_called()

    def test_maybe_reply_skipped_when_disabled(self):
        c = self._consumer({})  # no rag config → fall back to endpoint.chat
        with mock.patch.object(c, "_graphrag_answer") as ga:
            out = async_to_sync(c._maybe_graphrag_reply)("hi")
        self.assertIsNone(out)
        ga.assert_not_called()

    def test_maybe_reply_fails_soft(self):
        c = self._consumer({"rag": {"enabled": True}})
        with mock.patch.object(c, "_graphrag_answer", side_effect=RuntimeError("boom")):
            out = async_to_sync(c._maybe_graphrag_reply)("hi")
        self.assertIsNone(out)  # error → fall back to endpoint.chat, chat unaffected
