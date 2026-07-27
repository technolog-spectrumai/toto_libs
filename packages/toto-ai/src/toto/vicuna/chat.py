"""Ollama chat model resolution for Vicuna.

All Ollama chat model decisions go through here. Nothing outside this
module should hardcode a Qwen model string.

Settings:
    VICUNA_OLLAMA_HOST          default: http://localhost:11434
    VICUNA_CHAT_MODEL           default: qwen3:1.7b
    VICUNA_CHAT_MODEL_CHOICES   default: [qwen3:0.6b, qwen3:1.7b, qwen3:4b]
    VICUNA_CHAT_TEMPERATURE     default: 0.1
    VICUNA_CHAT_TIMEOUT         default: 180
"""
from __future__ import annotations

_HARDCODED_DEFAULT = "qwen3:1.7b"
_HARDCODED_CHOICES = ["qwen3:0.6b", "qwen3:1.7b", "qwen3:4b"]


def ollama_host() -> str:
    from django.conf import settings
    return getattr(settings, "VICUNA_OLLAMA_HOST", "http://localhost:11434")


def ollama_chat_model_choices() -> list[str]:
    from django.conf import settings
    choices = getattr(settings, "VICUNA_CHAT_MODEL_CHOICES", None)
    if (
        choices
        and isinstance(choices, list)
        and all(isinstance(c, str) and c for c in choices)
    ):
        return list(choices)
    return list(_HARDCODED_CHOICES)


def default_ollama_chat_model() -> str:
    from django.conf import settings
    model = getattr(settings, "VICUNA_CHAT_MODEL", _HARDCODED_DEFAULT)
    if model in ollama_chat_model_choices():
        return model
    return _HARDCODED_DEFAULT


def is_allowed_ollama_chat_model(model_name: str) -> bool:
    return bool(model_name) and model_name in ollama_chat_model_choices()


def installed_ollama_chat_models() -> list[str]:
    """Return allowed models that are currently pulled in Ollama.

    Prefers the DB-backed OllamaModel records (kept fresh by the vicuna admin
    sync action). Falls back to a live /api/tags query against VICUNA_OLLAMA_HOST
    when no OllamaServer records exist yet. Returns the full allowed list if
    Ollama is unreachable, so the admin form never breaks.
    """
    allowed = ollama_chat_model_choices()

    # DB-backed path: use synced OllamaModel records when servers are configured.
    try:
        from toto.vicuna.models import OllamaModel, OllamaServer
        if OllamaServer.objects.filter(is_active=True).exists():
            pulled = set(
                OllamaModel.objects.filter(server__is_active=True)
                .values_list("name", flat=True)
            )
            found = [m for m in allowed if m in pulled]
            return found if found else allowed
    except Exception:
        pass

    # Live fallback: query VICUNA_OLLAMA_HOST directly (settings-era compat).
    import json
    import urllib.request
    from django.conf import settings

    host = getattr(settings, "VICUNA_OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read())
        pulled = {m["name"] for m in (data.get("models") or [])}
        found = [m for m in allowed if m in pulled]
        return found if found else allowed
    except Exception:
        return allowed


def resolve_ollama_chat_model(profile=None) -> str:
    """Return the chat model to use for *profile*.

    Uses profile.model_name when it is in the allowed choices,
    otherwise falls back to the configured default.
    """
    default = default_ollama_chat_model()
    if profile is None:
        return default
    model_name = getattr(profile, "model_name", None) or ""
    return model_name if is_allowed_ollama_chat_model(model_name) else default
