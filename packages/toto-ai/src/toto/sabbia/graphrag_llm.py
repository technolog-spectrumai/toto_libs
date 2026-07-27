"""Build a ``neo4j-graphrag`` LLM from a sabbia :class:`Agent`.

Credentials stay in sabbia (which owns the gervazy vault): for OpenAI agents the
API key is decrypted from the sabbia vault and handed to ``OpenAILLM``; Ollama
agents point at the local host. Lazy-imports neo4j-graphrag so importing this
module never hard-requires the framework.
"""
from __future__ import annotations

from django.conf import settings


def build_llm(agent):
    """Return a neo4j-graphrag ``LLMInterface`` for ``agent``, or ``None`` when the
    endpoint type is unsupported or its key/config is unavailable."""
    model_params = {"temperature": agent.temperature}

    if agent.endpoint_type == agent.ENDPOINT_OPENAI:
        from neo4j_graphrag.llm import OpenAILLM

        from toto.sabbia import vault

        api_key = None
        if agent.connector is not None:
            try:
                api_key = agent.connector.decrypt_api_secret(vault_session=vault.open_session())
            except Exception:  # noqa: BLE001 — vault locked/uninitialized → no LLM
                return None
        if not api_key:
            return None
        return OpenAILLM(
            model_name=agent.model_name or "gpt-4.1-mini",
            model_params=model_params,
            api_key=api_key,
        )

    if agent.endpoint_type == agent.ENDPOINT_OLLAMA:
        from neo4j_graphrag.llm import OllamaLLM

        host = getattr(settings, "VICUNA_OLLAMA_HOST", "http://localhost:11434")
        return OllamaLLM(
            model_name=agent.model_name or "",
            model_params=model_params,
            host=host,
        )

    return None
