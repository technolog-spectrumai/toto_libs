"""Tests for toto.vicuna.chat model resolution and related session/command behaviour."""
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
        from toto.vicuna.chat import ollama_chat_model_choices
        choices = ollama_chat_model_choices()
        self.assertEqual(choices, ["qwen3:0.6b", "qwen3:1.7b", "qwen3:4b"])

    @override_settings(VICUNA_CHAT_MODEL_CHOICES=["qwen3:0.6b", "qwen3:4b"])
    def test_respects_settings_override(self):
        from toto.vicuna.chat import ollama_chat_model_choices
        self.assertEqual(ollama_chat_model_choices(), ["qwen3:0.6b", "qwen3:4b"])

    @override_settings(VICUNA_CHAT_MODEL_CHOICES=None)
    def test_falls_back_to_builtin_when_setting_is_none(self):
        from toto.vicuna.chat import ollama_chat_model_choices
        self.assertIn("qwen3:1.7b", ollama_chat_model_choices())

    @override_settings(VICUNA_CHAT_MODEL_CHOICES=[])
    def test_falls_back_to_builtin_when_setting_is_empty_list(self):
        from toto.vicuna.chat import ollama_chat_model_choices
        self.assertIn("qwen3:1.7b", ollama_chat_model_choices())


# ---------------------------------------------------------------------------
# default_ollama_chat_model
# ---------------------------------------------------------------------------

class DefaultOllamaChatModelTests(SimpleTestCase):

    @override_settings(VICUNA_CHAT_MODEL="qwen3:1.7b")
    def test_default_is_qwen3_1_7b(self):
        from toto.vicuna.chat import default_ollama_chat_model
        self.assertEqual(default_ollama_chat_model(), "qwen3:1.7b")

    @override_settings(VICUNA_CHAT_MODEL="qwen3:4b")
    def test_qwen3_4b_is_valid_default(self):
        from toto.vicuna.chat import default_ollama_chat_model
        self.assertEqual(default_ollama_chat_model(), "qwen3:4b")

    @override_settings(VICUNA_CHAT_MODEL="gpt-totally-made-up")
    def test_invalid_default_falls_back_to_1_7b(self):
        from toto.vicuna.chat import default_ollama_chat_model
        self.assertEqual(default_ollama_chat_model(), "qwen3:1.7b")

    @override_settings(VICUNA_CHAT_MODEL="qwen3:0.6b")
    def test_fallback_model_is_valid(self):
        from toto.vicuna.chat import default_ollama_chat_model
        self.assertEqual(default_ollama_chat_model(), "qwen3:0.6b")


# ---------------------------------------------------------------------------
# is_allowed_ollama_chat_model
# ---------------------------------------------------------------------------

class IsAllowedOllamaChatModelTests(SimpleTestCase):

    def test_allowed_models_return_true(self):
        from toto.vicuna.chat import is_allowed_ollama_chat_model
        for m in ("qwen3:0.6b", "qwen3:1.7b", "qwen3:4b"):
            with self.subTest(model=m):
                self.assertTrue(is_allowed_ollama_chat_model(m))

    def test_arbitrary_string_returns_false(self):
        from toto.vicuna.chat import is_allowed_ollama_chat_model
        self.assertFalse(is_allowed_ollama_chat_model("gpt-4o"))
        self.assertFalse(is_allowed_ollama_chat_model("llama3"))
        self.assertFalse(is_allowed_ollama_chat_model("LoboLightNLP"))

    def test_empty_string_returns_false(self):
        from toto.vicuna.chat import is_allowed_ollama_chat_model
        self.assertFalse(is_allowed_ollama_chat_model(""))


# ---------------------------------------------------------------------------
# resolve_ollama_chat_model
# ---------------------------------------------------------------------------

class ResolveOllamaChatModelTests(SimpleTestCase):

    @override_settings(VICUNA_CHAT_MODEL="qwen3:1.7b")
    def test_no_profile_returns_default(self):
        from toto.vicuna.chat import resolve_ollama_chat_model
        self.assertEqual(resolve_ollama_chat_model(None), "qwen3:1.7b")

    @override_settings(VICUNA_CHAT_MODEL="qwen3:1.7b")
    def test_profile_with_valid_model_name_is_used(self):
        from toto.vicuna.chat import resolve_ollama_chat_model
        profile = SimpleNamespace(model_name="qwen3:4b")
        self.assertEqual(resolve_ollama_chat_model(profile), "qwen3:4b")

    @override_settings(VICUNA_CHAT_MODEL="qwen3:1.7b")
    def test_profile_with_invalid_model_name_falls_back_to_default(self):
        from toto.vicuna.chat import resolve_ollama_chat_model
        profile = SimpleNamespace(model_name="LoboLightNLP")
        self.assertEqual(resolve_ollama_chat_model(profile), "qwen3:1.7b")

    @override_settings(VICUNA_CHAT_MODEL="qwen3:1.7b")
    def test_profile_with_empty_model_name_falls_back(self):
        from toto.vicuna.chat import resolve_ollama_chat_model
        profile = SimpleNamespace(model_name="")
        self.assertEqual(resolve_ollama_chat_model(profile), "qwen3:1.7b")

    @override_settings(VICUNA_CHAT_MODEL="qwen3:1.7b")
    def test_profile_with_none_model_name_falls_back(self):
        from toto.vicuna.chat import resolve_ollama_chat_model
        profile = SimpleNamespace(model_name=None)
        self.assertEqual(resolve_ollama_chat_model(profile), "qwen3:1.7b")

    @override_settings(VICUNA_CHAT_MODEL="totally-wrong")
    def test_invalid_default_setting_still_produces_1_7b(self):
        from toto.vicuna.chat import resolve_ollama_chat_model
        profile = SimpleNamespace(model_name="also-wrong")
        self.assertEqual(resolve_ollama_chat_model(profile), "qwen3:1.7b")

    @override_settings(VICUNA_CHAT_MODEL="qwen3:1.7b")
    def test_all_valid_choices_are_usable_via_profile(self):
        from toto.vicuna.chat import ollama_chat_model_choices, resolve_ollama_chat_model
        for m in ollama_chat_model_choices():
            with self.subTest(model=m):
                profile = SimpleNamespace(model_name=m)
                self.assertEqual(resolve_ollama_chat_model(profile), m)


# ---------------------------------------------------------------------------
# OllamaAgentSession passes resolved model to ChatOllama
# ---------------------------------------------------------------------------

class OllamaSessionModelResolutionTests(SimpleTestCase):

    @override_settings(VICUNA_CHAT_MODEL="qwen3:1.7b", VICUNA_CHAT_TEMPERATURE=0.1, VICUNA_CHAT_TIMEOUT=180)
    def test_session_uses_resolved_model_from_profile(self):
        """Profile with qwen3:4b should send qwen3:4b to ChatOllama."""
        from toto.steven.services.agent_session import OllamaAgentSession

        profile = SimpleNamespace(
            name="T", slug="t", is_active=True,
            model_name="qwen3:4b",
            temperature=0.1,
            system_prompt="",
            connector=SimpleNamespace(base_url="http://localhost:11434"),
        )
        session = OllamaAgentSession(profile)

        created_kwargs = {}

        class FakeChatOllama:
            def __init__(self, **kw):
                created_kwargs.update(kw)

        fake_lo = MagicMock()
        fake_lo.ChatOllama = FakeChatOllama

        with patch.dict("sys.modules", {"langchain_ollama": fake_lo}), \
             patch("toto.steven.services.agent_session._run_tool_loop", return_value="ok"), \
             patch("toto.steven.services.tools.tools_for_agent", return_value=[]):
            session.invoke("hi")

        self.assertEqual(created_kwargs["model"], "qwen3:4b")

    @override_settings(VICUNA_CHAT_MODEL="qwen3:1.7b", VICUNA_CHAT_TEMPERATURE=0.1, VICUNA_CHAT_TIMEOUT=180)
    def test_session_rejects_invalid_profile_model_falls_back_to_default(self):
        """Profile with LoboLightNLP should fall back to qwen3:1.7b."""
        from toto.steven.services.agent_session import OllamaAgentSession

        profile = SimpleNamespace(
            name="T", slug="t", is_active=True,
            model_name="LoboLightNLP",
            temperature=0.1,
            system_prompt="",
            connector=SimpleNamespace(base_url="http://localhost:11434"),
        )
        session = OllamaAgentSession(profile)

        created_kwargs = {}

        class FakeChatOllama:
            def __init__(self, **kw):
                created_kwargs.update(kw)

        fake_lo = MagicMock()
        fake_lo.ChatOllama = FakeChatOllama

        with patch.dict("sys.modules", {"langchain_ollama": fake_lo}), \
             patch("toto.steven.services.agent_session._run_tool_loop", return_value="ok"), \
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
        import inspect
        import toto.steven.services.agent_session as mod
        src = inspect.getsource(mod.RealAgentSession.invoke)
        self.assertNotIn("resolve_ollama_chat_model", src)

    def test_resolve_does_not_affect_openai_model_names(self):
        """resolve_ollama_chat_model is never called for OpenAI profiles."""
        from toto.vicuna.chat import resolve_ollama_chat_model
        profile = SimpleNamespace(model_name="openai:gpt-4.1-mini")
        result = resolve_ollama_chat_model(profile)
        self.assertEqual(result, "qwen3:1.7b")


# ---------------------------------------------------------------------------
# vicuna_pull_chat command
# ---------------------------------------------------------------------------

def _make_pull_cmd():
    from toto.vicuna.management.commands.vicuna_pull_chat import Command
    cmd = Command()
    cmd.stdout = StringIO()
    cmd.stderr = StringIO()
    cmd.style = MagicMock()
    cmd.style.ERROR = lambda s: s
    cmd.style.SUCCESS = lambda s: s
    cmd.style.WARNING = lambda s: s
    return cmd


_NO_GPU = {"available": False, "backend": "none", "details": "no GPU"}


class PullChatModelCommandTests(SimpleTestCase):

    @override_settings(
        VICUNA_OLLAMA_HOST="http://localhost:11434",
        VICUNA_CHAT_MODEL="qwen3:1.7b",
        VICUNA_CHAT_MODEL_CHOICES=["qwen3:0.6b", "qwen3:1.7b", "qwen3:4b"],
        VICUNA_REQUIRE_GPU=False,
        VICUNA_CHAT_TIMEOUT=30,
    )
    def test_default_pulls_configured_default(self):
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

        with patch("toto.vicuna.runtime.detect_gpu", return_value=_NO_GPU), \
             patch("urllib.request.urlopen", side_effect=fake):
            _make_pull_cmd().handle(**{"model": None, "all": False})

        self.assertIn("qwen3:1.7b", pulled)

    @override_settings(
        VICUNA_OLLAMA_HOST="http://localhost:11434",
        VICUNA_CHAT_MODEL="qwen3:1.7b",
        VICUNA_CHAT_MODEL_CHOICES=["qwen3:0.6b", "qwen3:1.7b", "qwen3:4b"],
        VICUNA_REQUIRE_GPU=False,
        VICUNA_CHAT_TIMEOUT=30,
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

        with patch("toto.vicuna.runtime.detect_gpu", return_value=_NO_GPU), \
             patch("urllib.request.urlopen", side_effect=fake):
            _make_pull_cmd().handle(**{"model": "qwen3:4b", "all": False})

        self.assertIn("qwen3:4b", pulled)
        self.assertNotIn("qwen3:0.6b", pulled)

    @override_settings(
        VICUNA_OLLAMA_HOST="http://localhost:11434",
        VICUNA_CHAT_MODEL="qwen3:1.7b",
        VICUNA_CHAT_MODEL_CHOICES=["qwen3:0.6b", "qwen3:1.7b", "qwen3:4b"],
        VICUNA_REQUIRE_GPU=False,
        VICUNA_CHAT_TIMEOUT=30,
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

        with patch("toto.vicuna.runtime.detect_gpu", return_value=_NO_GPU), \
             patch("urllib.request.urlopen", side_effect=fake):
            _make_pull_cmd().handle(**{"model": None, "all": True})

        self.assertIn("qwen3:0.6b", pulled)
        self.assertIn("qwen3:1.7b", pulled)
        self.assertIn("qwen3:4b", pulled)

    @override_settings(
        VICUNA_CHAT_MODEL_CHOICES=["qwen3:0.6b", "qwen3:1.7b", "qwen3:4b"],
        VICUNA_REQUIRE_GPU=False,
    )
    def test_unknown_model_raises_command_error(self):
        from django.core.management.base import CommandError
        with patch("toto.vicuna.runtime.detect_gpu", return_value=_NO_GPU):
            with self.assertRaises(CommandError) as ctx:
                _make_pull_cmd().handle(**{"model": "llama3:8b", "all": False})
        self.assertIn("llama3:8b", str(ctx.exception))
        self.assertIn("Allowed", str(ctx.exception))
