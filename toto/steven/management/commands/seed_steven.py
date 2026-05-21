from django.core.management.base import BaseCommand

from toto.gervazy.models import EncryptedSecret
from toto.steven.models import AgentConnector, AgentProfile, AgentTool


class Command(BaseCommand):
    help = "Create a starter Steven agent with calculator and current-time tools."

    def handle(self, *args, **options):
        api_secret = EncryptedSecret.objects.filter(
            purpose="openai-api-key", state="active"
        ).first()

        connector = (
            AgentConnector.objects.filter(slug="openai-chat").first()
            or AgentConnector.objects.filter(slug="openai-environment").first()
        )
        if connector is None:
            connector = AgentConnector(slug="openai-chat")

        connector.name = "OpenAI Chat"
        connector.slug = "openai-chat"
        connector.provider = AgentConnector.OPENAI
        connector.is_active = True
        if api_secret:
            connector.api_secret = api_secret
        connector.save()

        agent, created = AgentProfile.objects.get_or_create(
            slug="steven-default",
            defaults={
                "name": "Steven",
                "description": "A general-purpose AI agent managed by Toto Studio.",
                "connector": connector,
                "model_name": "openai:gpt-4.1-mini",
                "system_prompt": (
                    "You are Steven, an AI agent manager. Use tools when helpful, "
                    "explain what you did, and ask for clarification only when required."
                ),
                "is_active": True,
            },
        )
        if agent.connector_id != connector.id:
            agent.connector = connector
            agent.save(update_fields=["connector"])
        for key in ["calculator", "current_time", "echo"]:
            AgentTool.objects.get_or_create(agent=agent, key=key, defaults={"enabled": True})
        self.stdout.write(
            self.style.SUCCESS(f'{"Created" if created else "Updated"} {agent.name}')
        )
