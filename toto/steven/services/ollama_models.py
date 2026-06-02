"""Ollama chat model resolution for Steven.

All Ollama chat model decisions go through here. Nothing outside this
module should hardcode a Qwen model string.
"""
from __future__ import annotations

# The absolute fallback when settings are invalid or missing.
_HARDCODED_DEFAULT = "qwen3:1.7b"
_HARDCODED_CHOICES = ["qwen3:0.6b", "qwen3:1.7b", "qwen3:4b"]


def ollama_chat_model_choices() -> list[str]:
    """Return the list of allowed Ollama chat model names."""
    from django.conf import settings

    choices = getattr(settings, "STEVEN_OLLAMA_CHAT_MODEL_CHOICES", None)
    if (
        choices
        and isinstance(choices, list)
        and all(isinstance(c, str) and c for c in choices)
    ):
        return list(choices)
    return list(_HARDCODED_CHOICES)


def default_ollama_chat_model() -> str:
    """Return the configured default model, validated against choices.

    Falls back to ``qwen3:1.7b`` if the configured value is not in the
    allowed list.
    """
    from django.conf import settings

    model = getattr(settings, "STEVEN_OLLAMA_CHAT_MODEL", _HARDCODED_DEFAULT)
    if model in ollama_chat_model_choices():
        return model
    return _HARDCODED_DEFAULT


def is_allowed_ollama_chat_model(model_name: str) -> bool:
    """True when *model_name* is in the allowed choices."""
    return bool(model_name) and model_name in ollama_chat_model_choices()


def resolve_ollama_chat_model(profile=None) -> str:
    """Return the model to use for *profile*'s Ollama session.

    If ``profile.model_name`` is in the allowed choices, it is used.
    Otherwise the configured default is returned.
    Never allows arbitrary model names from the database/admin.
    """
    default = default_ollama_chat_model()
    if profile is None:
        return default
    model_name = getattr(profile, "model_name", None) or ""
    return model_name if is_allowed_ollama_chat_model(model_name) else default
