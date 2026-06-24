"""Tests for the pluggable embedding providers (Ollama/OpenAI)."""
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from toto.vicuna import embeddings as emb


@override_settings(VICUNA_EMBEDDINGS_ENABLED=True)
class EmbeddingProviderTests(SimpleTestCase):
    @override_settings(VICUNA_EMBEDDING_PROVIDER="ollama")
    def test_ollama_provider(self):
        with mock.patch.object(emb, "_ollama_embed", return_value=[[1.0, 2.0, 3.0]]) as m:
            self.assertEqual(emb.embed_text("hi"), [1.0, 2.0, 3.0])
        m.assert_called_once()

    @override_settings(VICUNA_EMBEDDING_PROVIDER="openai", OPENAI_API_KEY="sk-x",
                       VICUNA_OPENAI_EMBEDDING_MODEL="text-embedding-3-small")
    def test_openai_provider(self):
        client = mock.Mock()
        client.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1, 0.2])]
        )
        with mock.patch("openai.OpenAI", return_value=client) as OpenAI:
            vec = emb.embed_text("hi")
        self.assertEqual(vec, [0.1, 0.2])
        OpenAI.assert_called_once_with(api_key="sk-x")
        self.assertEqual(client.embeddings.create.call_args.kwargs["model"], "text-embedding-3-small")

    @override_settings(VICUNA_EMBEDDING_PROVIDER="openai", OPENAI_API_KEY="sk-x")
    def test_openai_dimension_probe(self):
        client = mock.Mock()
        client.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.0] * 7)]
        )
        with mock.patch("openai.OpenAI", return_value=client):
            self.assertEqual(emb.embedding_dimension(), 7)

    @override_settings(VICUNA_EMBEDDING_PROVIDER="openai", OPENAI_API_KEY="")
    def test_openai_requires_key(self):
        with self.assertRaises(emb.EmbeddingUnavailable):
            emb.embed_text("hi")

    @override_settings(VICUNA_EMBEDDING_PROVIDER="bogus")
    def test_unknown_provider_raises(self):
        with self.assertRaises(emb.EmbeddingUnavailable):
            emb.embed_text("hi")


class EmbeddingGateTests(SimpleTestCase):
    @override_settings(VICUNA_EMBEDDINGS_ENABLED=False)
    def test_disabled_raises(self):
        with self.assertRaises(emb.EmbeddingUnavailable):
            emb.embed_text("hi")

    @override_settings(VICUNA_EMBEDDINGS_ENABLED=False)
    def test_empty_input_short_circuits(self):
        self.assertEqual(emb.embed_texts([]), [])  # no backend hit, no raise
