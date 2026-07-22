import json
from urllib.request import Request, urlopen

from django.conf import settings
from django.urls import reverse

from .base import ChatEndpoint


class OllamaChatEndpoint(ChatEndpoint):
    """Talks to local Ollama indirectly, via the toto.vicuna chat endpoint."""

    type_key = "ollama"
    label = "Ollama (local, via Vicuna)"

    def chat(self, messages, **opts) -> str:
        base = getattr(
            settings, "SABBIA_INTERNAL_BASE_URL", "http://127.0.0.1:8000"
        ).rstrip("/")
        url = base + reverse("vicuna:chat")
        payload = json.dumps(
            {
                "model": self.agent.model_name,
                "messages": messages,
                "temperature": self.agent.temperature,
            }
        ).encode("utf-8")
        request = Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Internal-Token": getattr(settings, "VICUNA_INTERNAL_TOKEN", ""),
            },
        )
        timeout = int(
            self.config.get("timeout", getattr(settings, "VICUNA_CHAT_TIMEOUT", 180))
        )
        with urlopen(request, timeout=timeout) as resp:
            data = json.loads(resp.read())
        if data.get("error"):
            raise RuntimeError(data["error"])
        return data.get("reply", "")
