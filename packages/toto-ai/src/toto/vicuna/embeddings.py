"""Vicuna embedding service — the sole owner of embedding calls.

Pluggable provider via the strategy pattern: ``VICUNA_EMBEDDING_PROVIDER`` selects
``ollama`` (default, local Qwen) or ``openai``. Ravioli, sabbia, and any other app
import the public helpers here; they must not call Ollama/OpenAI directly.

Settings:
    VICUNA_EMBEDDINGS_ENABLED       default: False
    VICUNA_EMBEDDING_PROVIDER       default: ollama   (ollama | openai)
    VICUNA_EMBEDDING_MODEL          default: qwen3-embedding:0.6b   (ollama model)
    VICUNA_OLLAMA_HOST              default: http://localhost:11434
    VICUNA_EMBEDDING_TIMEOUT        default: 120                    (ollama only)
    VICUNA_OPENAI_EMBEDDING_MODEL   default: text-embedding-3-small (openai model)
    OPENAI_API_KEY                  (required for the openai provider)

NOTE: the vector index dimension is derived from the active provider (see
``embedding_dimension``), so switching providers requires re-running
``ravioli_reindex_embeddings`` (dims differ: qwen3 ≠ text-embedding-3-small=1536).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from django.conf import settings


class EmbeddingUnavailable(RuntimeError):
    """Raised when embeddings are disabled, misconfigured, or the backend is unreachable."""


def embeddings_enabled() -> bool:
    return bool(getattr(settings, "VICUNA_EMBEDDINGS_ENABLED", False))


def get_embedding_model_name() -> str:
    return getattr(settings, "VICUNA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")


def _ollama_embed(host: str, model: str, timeout: int, texts: list[str]) -> list[list[float]]:
    """Call Ollama /api/embed. Isolated for easy mocking in tests."""
    try:
        import ollama as _o
    except ImportError as exc:
        raise EmbeddingUnavailable(
            "ollama package is not installed. Run: pip install ollama"
        ) from exc

    try:
        client = _o.Client(host=host, timeout=float(timeout))
        response = client.embed(model=model, input=texts)
    except Exception as exc:
        raise EmbeddingUnavailable(f"Ollama embedding failed: {exc}") from exc

    embeddings = response.embeddings
    if not isinstance(embeddings, list):
        raise EmbeddingUnavailable(
            f"Unexpected Ollama response — 'embeddings' missing: {response!r}"
        )

    return [[float(v) for v in vec] for vec in embeddings]


# ---------------------------------------------------------------------------
# Provider strategies
# ---------------------------------------------------------------------------


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class OllamaEmbeddingProvider(EmbeddingProvider):
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        host = getattr(settings, "VICUNA_OLLAMA_HOST", "http://localhost:11434")
        model = get_embedding_model_name()
        timeout = int(getattr(settings, "VICUNA_EMBEDDING_TIMEOUT", 120))
        return _ollama_embed(host, model, timeout, texts)


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        api_key = (getattr(settings, "OPENAI_API_KEY", "") or "").strip()
        if not api_key:
            raise EmbeddingUnavailable(
                "OPENAI_API_KEY is not set — required for the 'openai' embedding provider."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise EmbeddingUnavailable("openai package is not installed.") from exc

        model = (getattr(settings, "VICUNA_OPENAI_EMBEDDING_MODEL", "") or "").strip() \
            or "text-embedding-3-small"
        try:
            client = OpenAI(api_key=api_key)
            response = client.embeddings.create(model=model, input=list(texts))
        except Exception as exc:
            raise EmbeddingUnavailable(f"OpenAI embedding failed: {exc}") from exc
        return [[float(v) for v in item.embedding] for item in response.data]


_PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    "ollama": OllamaEmbeddingProvider,
    "openai": OpenAIEmbeddingProvider,
}


def _provider() -> EmbeddingProvider:
    name = getattr(settings, "VICUNA_EMBEDDING_PROVIDER", "ollama")
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise EmbeddingUnavailable(f"Unsupported embedding provider: {name!r}.")
    return cls()


# ---------------------------------------------------------------------------
# Public API (unchanged signatures — consumers delegate through the provider)
# ---------------------------------------------------------------------------


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of strings. Returns [] for empty input without hitting a backend."""
    if not texts:
        return []
    if not embeddings_enabled():
        raise EmbeddingUnavailable("VICUNA_EMBEDDINGS_ENABLED is False.")
    return _provider().embed_texts(list(texts))


def embed_text(text: str) -> list[float]:
    """Embed a single string."""
    return embed_texts([text])[0]


def embedding_dimension() -> int:
    """Return the active provider's vector dimension by embedding a short probe."""
    return len(embed_text("dimension probe"))
