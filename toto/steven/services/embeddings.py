"""Backward-compat shim. Import from toto.vicuna.embeddings instead."""
from toto.vicuna.embeddings import (  # noqa: F401
    EmbeddingUnavailable,
    embeddings_enabled,
    get_embedding_model_name,
    _ollama_embed,
    embed_texts,
    embed_text,
    embedding_dimension,
)
