"""Steven embedding service — sole owner of Ollama embedding calls.

Ravioli and any other app may import from here; they must not talk to
Ollama directly.
"""
from __future__ import annotations

from django.conf import settings


class EmbeddingUnavailable(RuntimeError):
    """Raised when embeddings are disabled, misconfigured, or Ollama is unreachable."""


def embeddings_enabled() -> bool:
    return bool(getattr(settings, "STEVEN_EMBEDDINGS_ENABLED", False))


def get_embedding_model_name() -> str:
    return getattr(settings, "STEVEN_EMBEDDING_MODEL", "qwen3-embedding:0.6b")


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


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of strings. Returns [] for empty input without hitting Ollama."""
    if not texts:
        return []

    if not embeddings_enabled():
        raise EmbeddingUnavailable("STEVEN_EMBEDDINGS_ENABLED is False.")

    provider = getattr(settings, "STEVEN_EMBEDDING_PROVIDER", "ollama")
    if provider != "ollama":
        raise EmbeddingUnavailable(f"Unsupported embedding provider: {provider!r}.")

    host = getattr(settings, "STEVEN_OLLAMA_HOST", "http://localhost:11434")
    model = get_embedding_model_name()
    timeout = int(getattr(settings, "STEVEN_EMBEDDING_TIMEOUT", 120))

    return _ollama_embed(host, model, timeout, texts)


def embed_text(text: str) -> list[float]:
    """Embed a single string."""
    return embed_texts([text])[0]


def embedding_dimension() -> int:
    """Return the vector dimension by embedding a short probe string.

    Raises EmbeddingUnavailable if embeddings are disabled or Ollama is down.
    """
    return len(embed_text("dimension probe"))
