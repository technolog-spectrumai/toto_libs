# chat/echo_agent_ws_client.py

from toto.enigma.agent.base_client import BaseAgentWebsocketClient


class EchoAgentWebsocketClient(BaseAgentWebsocketClient):
    async def build_response(self, *, plaintext: bytes, envelope: dict) -> str:
        text = plaintext.decode("utf-8", errors="replace")
        return f"Agent received: {text}"