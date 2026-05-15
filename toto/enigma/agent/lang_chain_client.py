# chat/langchain_agent_ws_client.py

from from toto.enigma.agent.base_client import BaseAgentWebsocketClient


class LangChainAgentWebsocketClient(BaseAgentWebsocketClient):
    def __init__(self, *, chain, participant, **kwargs):
        super().__init__(participant_id=participant.id, **kwargs)
        self.chain = chain
        self.participant = participant

    async def build_response(self, *, plaintext: bytes, envelope: dict) -> str:
        text = plaintext.decode("utf-8", errors="replace")

        result = self.chain.invoke(
            {
                "message": text,
                "agent": self.participant.display_name,
                "room": self.participant.room.slug,
                "sender_id": envelope.get("sender_id"),
            }
        )

        if isinstance(result, dict):
            return result.get("text") or result.get("output") or ""

        return str(result)