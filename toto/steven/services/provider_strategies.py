"""Provider strategies for AgentConnector.

Each concrete strategy covers the full lifecycle for one provider:
  - test()           connection health check
  - create_session() return the right AgentSession for inference

REGISTRY is the single source of truth for supported providers.
AgentConnector.PROVIDER_CHOICES and clean() both derive from it.
To add a provider: write a class, add one line to REGISTRY. Nothing else changes.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from importlib.util import find_spec
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ProviderStrategy(ABC):
    label: str

    @abstractmethod
    def test(self, connector, api_key: str, timeout: int) -> dict:
        """Return {"ok": bool, "message": str}."""
        raise NotImplementedError

    @abstractmethod
    def create_session(self, profile):
        """Return an AgentSession ready for invoke()."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

class OpenAIProviderStrategy(ProviderStrategy):
    label = "OpenAI"

    def test(self, connector, api_key: str, timeout: int) -> dict:
        payload = json.dumps({
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": "Reply with ok."}],
            "max_tokens": 8,
        }).encode("utf-8")
        request = Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "toto-studio-steven/1.0",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                if 200 <= response.status < 300:
                    return {"ok": True, "message": f"OpenAI connection succeeded (HTTP {response.status})."}
                return {"ok": False, "message": f"OpenAI connection returned HTTP {response.status}."}
        except HTTPError as exc:
            detail = exc.read(300).decode("utf-8", errors="replace").strip()
            message = f"OpenAI connection failed with HTTP {exc.code}."
            if detail:
                message = f"{message} {detail}"
            return {"ok": False, "message": message}
        except URLError as exc:
            return {"ok": False, "message": f"OpenAI connection failed: {exc.reason}"}
        except TimeoutError:
            return {"ok": False, "message": f"OpenAI connection timed out after {timeout} seconds."}

    def create_session(self, profile):
        from toto.steven.services.agent_session import RealAgentSession, StubAgentSession
        if find_spec("langchain") is None:
            return StubAgentSession(profile, reason="LangChain is not installed in this environment.")
        return RealAgentSession(profile)


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

class OllamaProviderStrategy(ProviderStrategy):
    label = "Ollama local"

    def test(self, connector, api_key: str, timeout: int) -> dict:
        from toto.vicuna.chat import default_ollama_chat_model

        host = (getattr(connector, "base_url", None) or "http://localhost:11434").rstrip("/")

        try:
            with urlopen(f"{host}/api/tags", timeout=timeout) as resp:
                if not (200 <= resp.status < 300):
                    return {"ok": False, "message": f"Ollama /api/tags returned HTTP {resp.status}."}
        except (HTTPError, URLError) as exc:
            return {"ok": False, "message": f"Cannot reach Ollama at {host}: {exc}"}
        except TimeoutError:
            return {"ok": False, "message": f"Ollama connection timed out after {timeout}s."}

        model = default_ollama_chat_model()
        smoke_req = Request(
            f"{host}/api/chat",
            data=json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": "ok"}],
                "stream": False,
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(smoke_req, timeout=timeout) as resp:
                if 200 <= resp.status < 300:
                    return {"ok": True, "message": f"Ollama connection succeeded (model: {model})."}
                return {"ok": False, "message": f"Ollama /api/chat returned HTTP {resp.status}."}
        except (HTTPError, URLError, TimeoutError) as exc:
            return {"ok": False, "message": f"Ollama smoke test failed: {exc}"}

    def create_session(self, profile):
        from toto.steven.services.agent_session import OllamaAgentSession, StubAgentSession
        if find_spec("langchain_ollama") is None:
            return StubAgentSession(
                profile,
                reason="langchain-ollama is not installed. Run: pip install langchain-ollama",
            )
        return OllamaAgentSession(profile)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY: dict[str, ProviderStrategy] = {
    "openai": OpenAIProviderStrategy(),
    "ollama": OllamaProviderStrategy(),
}

# Ready-made Django choices list consumed by AgentConnector.PROVIDER_CHOICES.
PROVIDER_CHOICES: list[tuple[str, str]] = [(k, v.label) for k, v in REGISTRY.items()]
