"""Connection-test strategies for AgentConnector providers.

Each strategy implements one method:
    test(connector, api_key, timeout) -> {"ok": bool, "message": str}

REGISTRY is the single source of truth for which providers are supported.
AgentConnector.PROVIDER_CHOICES and clean() both derive from it.
To add a new provider: add a class below and register it in REGISTRY.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ConnectionTester(ABC):
    label: str

    @abstractmethod
    def test(self, connector, api_key: str, timeout: int) -> dict:
        raise NotImplementedError


class OpenAIConnectionTester(ConnectionTester):
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


class OllamaConnectionTester(ConnectionTester):
    label = "Ollama local"

    def test(self, connector, api_key: str, timeout: int) -> dict:
        from django.conf import settings
        from toto.vicuna.chat import default_ollama_chat_model

        host = getattr(settings, "VICUNA_OLLAMA_HOST", "http://localhost:11434").rstrip("/")

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


class RuleBasedConnectionTester(ConnectionTester):
    label = "Rule-based (no API key)"

    def test(self, connector, api_key: str, timeout: int) -> dict:
        return {"ok": True, "message": "Rule-based connector — no external connection needed."}


# Single source of truth: provider key → (tester, display label).
# AgentConnector.PROVIDER_CHOICES and clean() both derive from this dict.
REGISTRY: dict[str, ConnectionTester] = {
    "openai": OpenAIConnectionTester(),
    "ollama": OllamaConnectionTester(),
    "rule_based": RuleBasedConnectionTester(),
}

# Ready-made Django choices list for AgentConnector.PROVIDER_CHOICES.
PROVIDER_CHOICES: list[tuple[str, str]] = [(k, v.label) for k, v in REGISTRY.items()]


def test_connection(connector, api_key: str = "", timeout: int = 10) -> dict:
    tester = REGISTRY.get(connector.provider)
    if tester is None:
        return {
            "ok": False,
            "message": f"No connection tester registered for provider {connector.provider!r}.",
        }
    return tester.test(connector, api_key, timeout)
