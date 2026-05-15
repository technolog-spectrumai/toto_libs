# chat/management/commands/run_agent_ws.py

import asyncio

from django.core.management.base import BaseCommand

from toto.enigma.agent.echo_agent import EchoAgentWebsocketClient
from toto.enigma.agent.connection import EnigmaConnection
from toto.enigma.models import Participant


class Command(BaseCommand):
    help = "Run an agent websocket client for a room participant."

    def add_arguments(self, parser):
        parser.add_argument("--participant-id", required=True, type=int)
        parser.add_argument("--ws-url", required=True)

    def handle(self, *args, **options):
        participant = (
            Participant.objects
            .select_related("room", "agent")
            .get(
                id=options["participant_id"],
                agent__isnull=False,
                is_active=True,
            )
        )

        connection = EnigmaConnection.for_participant(participant)

        client = EchoAgentWebsocketClient(
            websocket_url=options["ws_url"],
            connection=connection,
            participant_id=participant.id,
        )

        asyncio.run(client.run_forever())