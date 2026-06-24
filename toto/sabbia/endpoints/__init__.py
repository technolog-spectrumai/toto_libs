"""Pluggable chat-endpoint strategies for sabbia agents.

Each endpoint type implements ``ChatEndpoint.chat(messages) -> str`` over HTTP.
Adding a new type = a new class + one line in ``REGISTRY``. ``ENDPOINT_CHOICES``
and the ``Agent.endpoint_type`` field choices update automatically.

This module must NOT import ``toto.sabbia.models`` at top level (models.py imports
``ENDPOINT_CHOICES`` from here).
"""
from .base import ChatEndpoint
from .ollama import OllamaChatEndpoint
from .openai import OpenAIChatEndpoint

REGISTRY: dict[str, type[ChatEndpoint]] = {
    OpenAIChatEndpoint.type_key: OpenAIChatEndpoint,
    OllamaChatEndpoint.type_key: OllamaChatEndpoint,
}

ENDPOINT_CHOICES = [(key, cls.label) for key, cls in REGISTRY.items()]


def get_endpoint(agent) -> ChatEndpoint:
    """Build the ChatEndpoint strategy for ``agent`` based on its ``endpoint_type``."""
    cls = REGISTRY.get(agent.endpoint_type)
    if cls is None:
        raise RuntimeError(f"Unknown sabbia endpoint type: {agent.endpoint_type!r}")
    return cls(agent)
