import asyncio

from django.core.management.base import BaseCommand

from toto.telegraph.agent.echo_client import EchoAgentWebsocketClient
from toto.telegraph.models import TelegraphMember


class Command(BaseCommand):
    help = "Run an agent websocket client for a channel member."

    def add_arguments(self, parser):
        parser.add_argument("--member-id", required=True, type=int)
        parser.add_argument("--ws-url", required=True)

    def handle(self, *args, **options):
        member = (
            TelegraphMember.objects
            .select_related("channel")
            .get(
                id=options["member_id"],
                is_active=True,
            )
        )

        client = EchoAgentWebsocketClient(
            websocket_url=options["ws_url"],
            connection=None,
            participant_id=member.id,
        )

        asyncio.run(client.run_forever())
