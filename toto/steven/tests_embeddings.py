"""Tests for toto.vicuna.embeddings and the pull management command."""
from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings


# ---------------------------------------------------------------------------
# embeddings_enabled / embed_texts disabled
# ---------------------------------------------------------------------------

class EmbeddingsDisabledTests(SimpleTestCase):

    @override_settings(VICUNA_EMBEDDINGS_ENABLED=False)
    def test_embed_texts_raises_when_disabled(self):
        from toto.vicuna.embeddings import EmbeddingUnavailable, embed_texts
        with self.assertRaises(EmbeddingUnavailable):
            embed_texts(["hello"])

    @override_settings(VICUNA_EMBEDDINGS_ENABLED=False)
    def test_embed_text_raises_when_disabled(self):
        from toto.vicuna.embeddings import EmbeddingUnavailable, embed_text
        with self.assertRaises(EmbeddingUnavailable):
            embed_text("hello")

    @override_settings(VICUNA_EMBEDDINGS_ENABLED=False)
    def test_embed_texts_empty_list_returns_empty_without_hitting_ollama(self):
        from toto.vicuna.embeddings import embed_texts
        self.assertEqual(embed_texts([]), [])

    @override_settings(VICUNA_EMBEDDINGS_ENABLED=True)
    def test_embed_texts_empty_list_returns_empty_when_enabled(self):
        from toto.vicuna.embeddings import embed_texts
        self.assertEqual(embed_texts([]), [])

    @override_settings(VICUNA_EMBEDDINGS_ENABLED=False)
    def test_embeddings_enabled_returns_false(self):
        from toto.vicuna.embeddings import embeddings_enabled
        self.assertFalse(embeddings_enabled())

    @override_settings(VICUNA_EMBEDDINGS_ENABLED=True)
    def test_embeddings_enabled_returns_true(self):
        from toto.vicuna.embeddings import embeddings_enabled
        self.assertTrue(embeddings_enabled())


# ---------------------------------------------------------------------------
# Unsupported provider
# ---------------------------------------------------------------------------

class EmbeddingsProviderTests(SimpleTestCase):

    @override_settings(
        VICUNA_EMBEDDINGS_ENABLED=True,
        VICUNA_EMBEDDING_PROVIDER="huggingface",
    )
    def test_unsupported_provider_raises_unavailable(self):
        from toto.vicuna.embeddings import EmbeddingUnavailable, embed_texts
        with self.assertRaises(EmbeddingUnavailable) as ctx:
            embed_texts(["hello"])
        self.assertIn("huggingface", str(ctx.exception))


# ---------------------------------------------------------------------------
# Ollama response parsing
# ---------------------------------------------------------------------------

class OllamaEmbedTests(SimpleTestCase):

    @override_settings(
        VICUNA_EMBEDDINGS_ENABLED=True,
        VICUNA_EMBEDDING_PROVIDER="ollama",
        VICUNA_OLLAMA_HOST="http://localhost:11434",
        VICUNA_EMBEDDING_MODEL="qwen3-embedding:0.6b",
        VICUNA_EMBEDDING_TIMEOUT=120,
    )
    def test_embed_texts_parses_ollama_response(self):
        with patch(
            "toto.vicuna.embeddings._ollama_embed",
            return_value=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
        ) as mock_fn:
            from toto.vicuna.embeddings import embed_texts
            result = embed_texts(["hello", "world"])

        self.assertEqual(result, [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
        mock_fn.assert_called_once_with(
            "http://localhost:11434",
            "qwen3-embedding:0.6b",
            120,
            ["hello", "world"],
        )

    @override_settings(
        VICUNA_EMBEDDINGS_ENABLED=True,
        VICUNA_EMBEDDING_PROVIDER="ollama",
    )
    def test_embed_text_returns_first_embedding(self):
        with patch(
            "toto.vicuna.embeddings._ollama_embed",
            return_value=[[0.9, 0.8, 0.7]],
        ):
            from toto.vicuna.embeddings import embed_text
            result = embed_text("single")

        self.assertEqual(result, [0.9, 0.8, 0.7])

    @override_settings(
        VICUNA_EMBEDDINGS_ENABLED=True,
        VICUNA_EMBEDDING_PROVIDER="ollama",
    )
    def test_ollama_connection_error_raises_unavailable(self):
        from toto.vicuna.embeddings import EmbeddingUnavailable

        with patch(
            "toto.vicuna.embeddings._ollama_embed",
            side_effect=EmbeddingUnavailable("Connection refused"),
        ):
            from toto.vicuna.embeddings import embed_texts
            with self.assertRaises(EmbeddingUnavailable) as ctx:
                embed_texts(["hello"])
        self.assertIn("Connection refused", str(ctx.exception))

    @override_settings(
        VICUNA_EMBEDDINGS_ENABLED=True,
        VICUNA_EMBEDDING_PROVIDER="ollama",
    )
    def test_embedding_dimension_uses_probe_string(self):
        with patch(
            "toto.vicuna.embeddings._ollama_embed",
            return_value=[[0.1] * 512],
        ):
            from toto.vicuna.embeddings import embedding_dimension
            dim = embedding_dimension()

        self.assertEqual(dim, 512)


# ---------------------------------------------------------------------------
# _ollama_embed with mocked ollama SDK
# ---------------------------------------------------------------------------

class OllamaSDKTests(SimpleTestCase):

    def test_ollama_embed_calls_sdk_client(self):
        """_ollama_embed calls ollama.Client(host=...).embed(model=..., input=...)."""
        from toto.vicuna.embeddings import _ollama_embed

        mock_response = MagicMock()
        mock_response.embeddings = [[1.0, 2.0]]

        mock_client_instance = MagicMock()
        mock_client_instance.embed.return_value = mock_response

        mock_ollama = MagicMock()
        mock_ollama.Client.return_value = mock_client_instance

        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            result = _ollama_embed(
                "http://localhost:11434", "qwen3-embedding:0.6b", 30, ["test"]
            )

        self.assertEqual(result, [[1.0, 2.0]])
        mock_ollama.Client.assert_called_once_with(
            host="http://localhost:11434", timeout=30.0
        )
        mock_client_instance.embed.assert_called_once_with(
            model="qwen3-embedding:0.6b", input=["test"]
        )

    def test_ollama_embed_raises_unavailable_on_sdk_error(self):
        from toto.vicuna.embeddings import EmbeddingUnavailable, _ollama_embed

        mock_ollama = MagicMock()
        mock_ollama.Client.side_effect = RuntimeError("boom")

        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            with self.assertRaises(EmbeddingUnavailable):
                _ollama_embed("http://localhost:11434", "model", 30, ["x"])


# ---------------------------------------------------------------------------
# vicuna_pull_embeddings command
# ---------------------------------------------------------------------------

class PullCommandTests(SimpleTestCase):

    def _make_command(self):
        from toto.vicuna.management.commands.vicuna_pull_embeddings import Command
        cmd = Command()
        cmd.stdout = StringIO()
        cmd.stderr = StringIO()
        cmd.style = MagicMock()
        cmd.style.ERROR = lambda s: s
        cmd.style.SUCCESS = lambda s: s
        return cmd

    @override_settings(
        VICUNA_OLLAMA_HOST="http://localhost:11434",
        VICUNA_EMBEDDING_MODEL="qwen3-embedding:0.6b",
    )
    def test_pull_posts_correct_payload_to_api_pull(self):
        captured_request = {}

        def fake_urlopen(req, timeout=None):
            captured_request["url"] = req.full_url
            captured_request["data"] = json.loads(req.data)
            captured_request["method"] = req.method
            ctx = MagicMock()
            ctx.__enter__ = lambda s: s
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.read.return_value = json.dumps({"status": "success"}).encode()
            return ctx

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self._make_command().handle()

        self.assertEqual(captured_request["url"], "http://localhost:11434/api/pull")
        self.assertEqual(captured_request["data"]["model"], "qwen3-embedding:0.6b")
        self.assertFalse(captured_request["data"]["stream"])
        self.assertEqual(captured_request["method"], "POST")

    @override_settings(
        VICUNA_OLLAMA_HOST="http://localhost:11434",
        VICUNA_EMBEDDING_MODEL="qwen3-embedding:0.6b",
    )
    def test_pull_exits_nonzero_when_ollama_unreachable(self):
        import urllib.error

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                self._make_command().handle()

        self.assertEqual(ctx.exception.code, 1)

    def test_get_embedding_model_name_uses_setting(self):
        with override_settings(VICUNA_EMBEDDING_MODEL="custom-model:1b"):
            from toto.vicuna.embeddings import get_embedding_model_name
            self.assertEqual(get_embedding_model_name(), "custom-model:1b")
