from toto.core.ingress import IngressCommand
from toto.gervazy.models import EncryptedSecret
from django.contrib.auth import get_user_model

from toto.steven.models import AgentConnector, AgentProfile, AgentTool

User = get_user_model()


class Command(IngressCommand):
    help = "Seed Steven with a starter agent and default tools"

    def process(self):
        if not self.full:
            return

        self.stdout.write(self.style.WARNING("Seeding Steven..."))

        api_secret = EncryptedSecret.objects.filter(
            purpose="openai-api-key", state="active"
        ).first()
        if api_secret is None:
            self.stdout.write(
                self.style.WARNING(
                    "No active Gervazy EncryptedSecret with purpose 'openai-api-key' found. "
                    "Create one in the Gervazy admin before running Steven agents."
                )
            )

        connector = (
            AgentConnector.objects.filter(slug="openai-chat").first()
            or AgentConnector.objects.filter(slug="openai-environment").first()
        )
        connector_created = connector is None
        if connector_created:
            connector = AgentConnector(slug="openai-chat")

        connector.name = "OpenAI Chat"
        connector.slug = "openai-chat"
        connector.provider = AgentConnector.OPENAI
        connector.is_active = True
        if api_secret:
            connector.api_secret = api_secret
        connector.save()

        if connector_created:
            self.stdout.write(self.style.SUCCESS("Created connector: OpenAI Chat"))
        else:
            self.stdout.write(self.style.WARNING("Connector already exists: OpenAI Chat"))

        agent, created = AgentProfile.objects.get_or_create(
            slug="steven-default",
            defaults={
                "name": "Steven Default",
                "description": "A general-purpose AI agent managed by Toto Studio.",
                "connector": connector,
                "system_prompt": (
                    "You are Steven, an AI agent manager inside Toto Studio. "
                    "Use tools when helpful, explain what you did, and ask for clarification only when required."
                ),
            },
        )

        if created:
            self.stdout.write(self.style.SUCCESS("Created agent: Steven Default"))
        else:
            self.stdout.write(self.style.WARNING("Agent already exists: Steven Default"))

        agent_user, user_created = User.objects.get_or_create(
            username="agent_steven_default",
            defaults={
                "email": "steven-default@agents.local",
                "is_active": True,
            },
        )
        if user_created:
            agent_user.set_unusable_password()
            agent_user.save(update_fields=["password"])
            self.stdout.write(self.style.SUCCESS("Created websocket user: agent_steven_default"))
        elif not agent_user.is_active:
            agent_user.is_active = True
            agent_user.save(update_fields=["is_active"])

        if agent.user_id != agent_user.id:
            agent.user = agent_user
            agent.save(update_fields=["user"])
            self.stdout.write(self.style.SUCCESS("Linked Steven Default to websocket user"))

        if agent.connector_id != connector.id:
            agent.connector = connector
            agent.save(update_fields=["connector"])
            self.stdout.write(self.style.SUCCESS("Attached connector to Steven Default"))

        for key in ["calculator", "current_time", "echo"]:
            tool, tool_created = AgentTool.objects.get_or_create(
                agent=agent,
                key=key,
                defaults={"enabled": True},
            )

            if tool_created:
                self.stdout.write(self.style.SUCCESS(f"Enabled tool: {tool.get_key_display()}"))
            else:
                self.stdout.write(self.style.WARNING(f"Tool already exists: {tool.get_key_display()}"))

        self.stdout.write(self.style.SUCCESS("Steven seeding complete."))
