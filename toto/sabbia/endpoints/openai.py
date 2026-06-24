import json
from urllib.request import Request, urlopen

from .base import ChatEndpoint


class OpenAIChatEndpoint(ChatEndpoint):
    """Talks to the OpenAI Chat Completions API. Key decrypted from the sabbia vault."""

    type_key = "openai"
    label = "OpenAI ChatGPT"

    DEFAULT_BASE_URL = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-4.1-mini"

    def _api_key(self) -> str:
        from toto.sabbia import vault

        connector = self.agent.connector
        if connector is None:
            raise RuntimeError(
                f'Agent "{self.agent.name}" has no connector for OpenAI credentials.'
            )
        return connector.decrypt_api_secret(vault_session=vault.open_session())

    def chat(self, messages, **opts) -> str:
        base = (self.config.get("base_url") or self.DEFAULT_BASE_URL).rstrip("/")
        payload = json.dumps(
            {
                "model": self.agent.model_name or self.DEFAULT_MODEL,
                "messages": messages,
                "temperature": self.agent.temperature,
            }
        ).encode("utf-8")
        request = Request(
            f"{base}/chat/completions",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key()}",
                "Content-Type": "application/json",
                "User-Agent": "toto-sabbia/1.0",
            },
        )
        timeout = int(self.config.get("timeout", 60))
        with urlopen(request, timeout=timeout) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
