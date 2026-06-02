"""Tests for toto.steven.services.ollama_models and related command/session behaviour."""
from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings


# ---------------------------------------------------------------------------
# ollama_chat_model_choices
# ---------------------------------------------------------------------------

class OllamaChatModelChoicesTests(SimpleTestCase):

    def test_returns_three_qwen_models_by_default(self):
        from toto.steven.services.ollama_models import ollama_chat_model_choices
        choices = ollama_chat_model_choices()
        self.assertEqual(choices, ["qwen3:0.6b", "qwen3:1.7b", "qwen3:4b"])

    @override_settings(STEVEN_OLLAMA_CHAT_MODEL_CHOICES=["qwen3:0.6b", "qwen3:4b"])
    def test_respects_settings_override(self):
        from toto.steven.services.ollama_models import ollama_chat_model_choices
        self.assertEqual(ollama_chat_model_choices(), ["qwen3:0.6b", "qwen3:4b"])

    @override_settings(STEVEN_OLLAMA_CHAT_MODEL_CHOICES=None)
    def test_falls_back_to_builtin_when_setting_is_none(self):
        from toto.steven.services.ollama_models import ollama_chat_model_choices
        self.assertIn("qwen3:1.7b", ollama_chat_model_choices())

    @override_settings(STEVEN_OLLAMA_CHAT_MODEL_CHOICES=[])
    def test_falls_back_to_builtin_when_setting_is_empty_list(self):
        from toto.steven.services.ollama_models import ollama_chat_model_choices
        self.assertIn("qwen3:1.7b", ollama_chat_model_choices())


# ---------------------------------------------------------------------------
# default_ollama_chat_model
# ---------------------------------------------------------------------------

class DefaultOllamaChatModelTests(SimpleTestCase):

    @override_settings(STEVEN_OLLAMA_CHAT_MODEL="qwen3:1.7b")
    def test_default_is_qwen3_1_7b(self):
        from toto.steven.services.ollama_models import default_ollama_chat_model
        self.assertEqual(default_ollama_chat_model(), "qwen3:1.7b")

    @override_settings(STEVEN_OLLAMA_CHAT_MODEL="qwen3:4b")
    def test_qwen3_4b_is_valid_default(self):
        from toto.steven.services.ollama_models import default_ollama_chat_model
        self.assertEqual(default_ollama_chat_model(), "qwen3:4b")

    @override_settings(STEVEN_OLLAMA_CHAT_MODEL="gpt-totally-made-up")
    def test_invalid_default_falls_back_to_1_7b(self):
        from toto.steven.services.ollama_models import default_ollama_chat_model
        self.assertEqual(default_ollama_chat_model(), "qwen3:1.7b")

    @override_settings(STEVEN_OLLAMA_CHAT_MODEL="qwen3:0.6b")
    def test_fallback_model_is_valid(self):
        from toto.steven.services.ollama_models import default_ollama_chat_model
        self.assertEqual(default_ollama_chat_model(), "qwen3:0.6b")


# ---------------------------------------------------------------------------
# is_allowed_ollama_chat_model
# ---------------------------------------------------------------------------

class IsAllowedOllamaChatModelTests(SimpleTestCase):

    def test_allowed_models_return_true(self):
        from toto.steven.services.ollama_models import is_allowed_ollama_chat_model
        for m in ("qwen3:0.6b", "qwen3:1.7b", "qwen3:4b"):
            with self.subTest(model=m):
                self.assertTrue(is_allowed_ollama_chat_model(m))

    def test_arbitrary_string_returns_false(self):
        from toto.steven.services.ollama_models import is_allowed_ollama_chat_model
        self.assertFalse(is_allowed_ollama_chat_model("gpt-4o"))
        self.assertFalse(is_allowed_ollama_chat_model("llama3"))
        self.assertFalse(is_allowed_ollama_chat_model("LoboLightNLP"))

    def test_empty_string_returns_false(self):
        from toto.steven.services.ollama_models import is_allowed_ollama_chat_model
        self.assertFalse(is_allowed_ollama_chat_model(""))


# ---------------------------------------------------------------------------
# resolve_ollama_chat_model
# ---------------------------------------------------------------------------

class ResolveOllamaChatModelTests(SimpleTestCase):

    @override_settings(STEVEN_OLLAMA_CHAT_MODEL="qwen3:1.7b")
    def test_no_profile_returns_default(self):
        from toto.steven.services.ollama_models import resolve_ollama_chat_model
        self.assertEqual(resolve_ollama_chat_model(None), "qwen3:1.7b")

    @override_settings(STEVEN_OLLAMA_CHAT_MODEL="qwen3:1.7b")
    def test_profile_with_valid_model_name_is_used(self):
        from toto.steven.services.ollama_models import resolve_ollama_chat_model
        profile = SimpleNamespace(model_name="qwen3:4b")
        self.assertEqual(resolve_ollama_chat_model(profile), "qwen3:4b")

    @override_settings(STEVEN_OLLAMA_CHAT_MODEL="qwen3:1.7b")
    def test_profile_with_invalid_model_name_falls_back_to_default(self):
        from toto.steven.services.ollama_models import resolve_ollama_chat_model
        profile = SimpleNamespace(model_name="LoboLightNLP")
        self.assertEqual(resolve_ollama_chat_model(profile), "qwen3:1.7b")

    @override_settings(STEVEN_OLLAMA_CHAT_MODEL="qwen3:1.7b")
    def test_profile_with_empty_model_name_falls_back(self):
        from toto.steven.services.ollama_models import resolve_ollama_chat_model
        profile = SimpleNamespace(model_name="")
        self.assertEqual(resolve_ollama_chat_model(profile), "qwen3:1.7b")

    @override_settings(STEVEN_OLLAMA_CHAT_MODEL="qwen3:1.7b")
    def test_profile_with_none_model_name_falls_back(self):
        from toto.steven.services.ollama_models import resolve_ollama_chat_model
        profile = SimpleNamespace(model_name=None)
        self.assertEqual(resolve_ollama_chat_model(profile), "qwen3:1.7b")

    @override_settings(STEVEN_OLLAMA_CHAT_MODEL="totally-wrong")
    def test_invalid_default_setting_still_produces_1_7b(self):
        from toto.steven.services.ollama_models import resolve_ollama_chat_model
        profile = SimpleNamespace(model_name="also-wrong")
        self.assertEqual(resolve_ollama_chat_model(profile), "qwen3:1.7b")

    @override_settings(STEVEN_OLLAMA_CHAT_MODEL="qwen3:1.7b")
    def test_all_valid_choices_are_usable_via_profile(self):
        from toto.steven.services.ollama_models import (
            ollama_chat_model_choices,
            resolve_ollama_chat_model,
        )
        for m in ollama_chat_model_choices():
            with self.subTest(model=m):
                profile = SimpleNamespace(model_name=m)
                self.assertEqual(resolve_ollama_chat_model(profile), m)


# ---------------------------------------------------------------------------
# OllamaAgentSession passes resolved model to ChatOllama
# ---------------------------------------------------------------------------

class OllamaSessionModelResolutionTests(SimpleTestCase):

    @override_settings(
        STEVEN_OLLAMA_CHAT_MODEL="qwen3:1.7b",
        STEVEN_OLLAMA_HOST="http://localhost:11434",
        STEVEN_OLLAMA_CHAT_TEMPERATURE=0.1,
        STEVEN_OLLAMA_CHAT_TIMEOUT=180,
    )
    def test_session_uses_resolved_model_from_profile(self):
        """Profile with qwen3:4b should send qwen3:4b to ChatOllama."""
        from toto.steven.services.agent_session import OllamaAgentSession

        profile = SimpleNamespace(
            name="T", slug="t", is_active=True,
            model_name="qwen3:4b",
            temperature=0.1,
            system_prompt="",
        )
        session = OllamaAgentSession(profile)

        created_kwargs = {}

        class FakeChatOllama:
            def __init__(self, **kw):
                created_kwargs.update(kw)

        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"messages": [MagicMock(content="ok")]}

        fake_lo = MagicMock()
        fake_lo.ChatOllama = FakeChatOllama

        with patch.dict("sys.modules", {"langchain_ollama": fake_lo}), \
             patch("langchain.agents.create_agent", return_value=mock_agent), \
             patch("toto.steven.services.tools.tools_for_agent", return_value=[]):
            session.invoke("hi")

        self.assertEqual(created_kwargs["model"], "qwen3:4b")

    @override_settings(
        STEVEN_OLLAMA_CHAT_MODEL="qwen3:1.7b",
        STEVEN_OLLAMA_HOST="http://localhost:11434",
        STEVEN_OLLAMA_CHAT_TEMPERATURE=0.1,
        STEVEN_OLLAMA_CHAT_TIMEOUT=180,
    )
    def test_session_rejects_invalid_profile_model_falls_back_to_default(self):
        """Profile with LoboLightNLP should fall back to qwen3:1.7b."""
        from toto.steven.services.agent_session import OllamaAgentSession

        profile = SimpleNamespace(
            name="T", slug="t", is_active=True,
            model_name="LoboLightNLP",
            temperature=0.1,
            system_prompt="",
        )
        session = OllamaAgentSession(profile)

        created_kwargs = {}

        class FakeChatOllama:
            def __init__(self, **kw):
                created_kwargs.update(kw)

        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"messages": [MagicMock(content="ok")]}

        fake_lo = MagicMock()
        fake_lo.ChatOllama = FakeChatOllama

        with patch.dict("sys.modules", {"langchain_ollama": fake_lo}), \
             patch("langchain.agents.create_agent", return_value=mock_agent), \
             patch("toto.steven.services.tools.tools_for_agent", return_value=[]):
            session.invoke("hi")

        self.assertEqual(created_kwargs["model"], "qwen3:1.7b")


# ---------------------------------------------------------------------------
# OpenAI provider unaffected
# ---------------------------------------------------------------------------

class OpenAIUnaffectedTests(SimpleTestCase):

    def test_openai_session_does_not_use_ollama_models(self):
        from toto.steven.services.agent_session import RealAgentSession
        self.assertTrue(issubclass(RealAgentSession, object))
        # RealAgentSession.invoke uses init_chat_model(profile.model_name),
        # not resolve_ollama_chat_model. Verify no import of ollama_models there.
        import inspect
        import toto.steven.services.agent_session as mod
        src = inspect.getsource(mod.RealAgentSession.invoke)
        self.assertNotIn("resolve_ollama_chat_model", src)

    def test_resolve_does_not_affect_openai_model_names(self):
        """resolve_ollama_chat_model is never called for OpenAI profiles."""
        from toto.steven.services.ollama_models import resolve_ollama_chat_model
        # For an OpenAI-style profile, the invalid model falls back to default
        # (but that code path is never reached in a real OpenAI session).
        profile = SimpleNamespace(model_name="openai:gpt-4.1-mini")
        result = resolve_ollama_chat_model(profile)
        # openai:gpt-4.1-mini is not in choices → falls back to 1.7b
        self.assertEqual(result, "qwen3:1.7b")


# ---------------------------------------------------------------------------
# steven_pull_chat_model command
# ---------------------------------------------------------------------------

def _make_pull_cmd():
    from toto.steven.management.commands.steven_pull_chat_model import Command
    cmd = Command()
    cmd.stdout = StringIO()
    cmd.stderr = StringIO()
    cmd.style = MagicMock()
    cmd.style.ERROR = lambda s: s
    cmd.style.SUCCESS = lambda s: s
    cmd.style.WARNING = lambda s: s
    return cmd


def _fake_urlopen_success(model_responses=None):
    """Return a urlopen side_effect that succeeds for all requests."""
    def fake(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        if "/api/pull" in url:
            body = json.dumps({"status": "success"}).encode()
        else:
            body = json.dumps({"message": {"content": "OK"}}).encode()
        ctx = MagicMock()
        ctx.__enter__ = lambda s: s
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.read.return_value = body
        return ctx
    return fake


_NO_GPU = {"available": False, "backend": "none", "details": "no GPU"}


class PullChatModelCommandTests(SimpleTestCase):

    @override_settings(
        STEVEN_OLLAMA_HOST="http://localhost:11434",
        STEVEN_OLLAMA_CHAT_MODEL="qwen3:1.7b",
        STEVEN_OLLAMA_CHAT_MODEL_CHOICES=["qwen3:0.6b", "qwen3:1.7b", "qwen3:4b"],
        STEVEN_OLLAMA_REQUIRE_GPU=False,
        STEVEN_OLLAMA_CHAT_TIMEOUT=30,
    )
    def test_default_pulls_configured_default(self):
        pulled = []

        def fake(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "/api/pull" in url:
                body = json.loads(req.data)
                pulled.append(body["model"])
            ctx = MagicMock()
            ctx.__enter__ = lambda s: s
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.read.return_value = json.dumps({"status": "success", "message": {"content": "OK"}}).encode()
            return ctx

        with patch("toto.steven.services.ollama_runtime.detect_gpu", return_value=_NO_GPU), \
             patch("urllib.request.urlopen", side_effect=fake):
            _make_pull_cmd().handle(**{"model": None, "all": False})

        self.assertIn("qwen3:1.7b", pulled)

    @override_settings(
        STEVEN_OLLAMA_HOST="http://localhost:11434",
        STEVEN_OLLAMA_CHAT_MODEL="qwen3:1.7b",
        STEVEN_OLLAMA_CHAT_MODEL_CHOICES=["qwen3:0.6b", "qwen3:1.7b", "qwen3:4b"],
        STEVEN_OLLAMA_REQUIRE_GPU=False,
        STEVEN_OLLAMA_CHAT_TIMEOUT=30,
    )
    def test_model_option_pulls_specific_model(self):
        pulled = []

        def fake(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "/api/pull" in url:
                pulled.append(json.loads(req.data)["model"])
            ctx = MagicMock()
            ctx.__enter__ = lambda s: s
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.read.return_value = json.dumps({"status": "success", "message": {"content": "OK"}}).encode()
            return ctx

        with patch("toto.steven.services.ollama_runtime.detect_gpu", return_value=_NO_GPU), \
             patch("urllib.request.urlopen", side_effect=fake):
            _make_pull_cmd().handle(**{"model": "qwen3:4b", "all": False})

        self.assertIn("qwen3:4b", pulled)
        self.assertNotIn("qwen3:0.6b", pulled)

    @override_settings(
        STEVEN_OLLAMA_HOST="http://localhost:11434",
        STEVEN_OLLAMA_CHAT_MODEL="qwen3:1.7b",
        STEVEN_OLLAMA_CHAT_MODEL_CHOICES=["qwen3:0.6b", "qwen3:1.7b", "qwen3:4b"],
        STEVEN_OLLAMA_REQUIRE_GPU=False,
        STEVEN_OLLAMA_CHAT_TIMEOUT=30,
    )
    def test_all_option_pulls_all_three_models(self):
        pulled = []

        def fake(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "/api/pull" in url:
                pulled.append(json.loads(req.data)["model"])
            ctx = MagicMock()
            ctx.__enter__ = lambda s: s
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.read.return_value = json.dumps({"status": "success", "message": {"content": "OK"}}).encode()
            return ctx

        with patch("toto.steven.services.ollama_runtime.detect_gpu", return_value=_NO_GPU), \
             patch("urllib.request.urlopen", side_effect=fake):
            _make_pull_cmd().handle(**{"model": None, "all": True})

        self.assertIn("qwen3:0.6b", pulled)
        self.assertIn("qwen3:1.7b", pulled)
        self.assertIn("qwen3:4b", pulled)

    @override_settings(
        STEVEN_OLLAMA_CHAT_MODEL_CHOICES=["qwen3:0.6b", "qwen3:1.7b", "qwen3:4b"],
        STEVEN_OLLAMA_REQUIRE_GPU=False,
    )
    def test_unknown_model_raises_command_error(self):
        from django.core.management.base import CommandError
        with patch("toto.steven.services.ollama_runtime.detect_gpu", return_value=_NO_GPU):
            with self.assertRaises(CommandError) as ctx:
                _make_pull_cmd().handle(**{"model": "llama3:8b", "all": False})
        self.assertIn("llama3:8b", str(ctx.exception))
        self.assertIn("Allowed", str(ctx.exception))
