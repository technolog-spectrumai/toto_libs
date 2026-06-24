from __future__ import annotations

from abc import ABC, abstractmethod


class ChatEndpoint(ABC):
    """One concrete chat transport for an agent.

    Stateless; constructed per agent. ``chat()`` is synchronous (it does blocking
    HTTP) and is invoked from the async consumer via ``sync_to_async``.
    """

    type_key: str = ""
    label: str = ""

    def __init__(self, agent):
        self.agent = agent
        self.config = agent.endpoint_config or {}

    @abstractmethod
    def chat(self, messages: list[dict], **opts) -> str:
        """Return the assistant reply for ``messages``.

        ``messages`` is ``[{"role": "system"|"user"|"assistant", "content": str}, ...]``.
        """
        raise NotImplementedError
