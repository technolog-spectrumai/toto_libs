"""Backward-compat shim. Import from toto.vicuna.chat instead."""
from toto.vicuna.chat import (  # noqa: F401
    ollama_chat_model_choices,
    default_ollama_chat_model,
    is_allowed_ollama_chat_model,
    resolve_ollama_chat_model,
)
