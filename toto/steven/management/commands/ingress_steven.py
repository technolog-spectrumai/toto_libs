from django.contrib.auth import get_user_model

from toto.core.ingress import IngressCommand
from toto.steven.models import AgentConnector, AgentProfile, AgentTool

User = get_user_model()

_DEFAULT_SLUG = "steven-default"


class Command(IngressCommand):
    help = "Seed Steven with a starter agent and default tools"

    def process(self):
        self.stdout.write(self.style.WARNING("Seeding Steven..."))

        # Always create the default agent so the floating widget works
        # even without FULL_INGRESS (it falls back to stub mode without a connector).
        agent = self._ensure_default_agent()

        if not self.full:
            self.stdout.write(
                self.style.WARNING(
                    "  Skipping connector + tool setup (FULL_INGRESS not set). "
                    "The chat widget will run in stub / debug mode."
                )
            )
            return

        self._setup_connector(agent)
        self._seed_tools(agent)
        self.stdout.write(self.style.SUCCESS("Steven seeding complete."))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_default_agent(self):
        agent_user, user_created = User.objects.get_or_create(
            username="agent_steven_default",
            defaults={"email": "steven-default@agents.local", "is_active": True},
        )
        if user_created:
            agent_user.set_unusable_password()
            agent_user.save(update_fields=["password"])
            self.stdout.write(self.style.SUCCESS("  Created websocket user: agent_steven_default"))
        elif not agent_user.is_active:
            agent_user.is_active = True
            agent_user.save(update_fields=["is_active"])

        # Always provision a rule-based connector so the widget works without an API key.
        rb_connector, rb_created = AgentConnector.objects.get_or_create(
            slug="rule-based",
            defaults={
                "name": "Rule-based",
                "provider": AgentConnector.RULE_BASED,
                "auth_type": "none",
                "is_active": True,
            },
        )
        if rb_created:
            self.stdout.write(self.style.SUCCESS("  Created connector: Rule-based"))
        else:
            self.stdout.write(self.style.WARNING("  Connector already exists: Rule-based"))

        agent, created = AgentProfile.objects.get_or_create(
            slug=_DEFAULT_SLUG,
            defaults={
                "name": "Steven",
                "description": "A general-purpose AI agent managed by Toto Studio.",
                "user": agent_user,
                "connector": rb_connector,
                "system_prompt": (
                    "You are Steven, an AI agent manager inside Toto Studio. "
                    "Use tools when helpful, explain what you did, and ask "
                    "for clarification only when truly required."
                ),
                "is_active": True,
            },
        )

        if created:
            self.stdout.write(self.style.SUCCESS("  Created agent: Steven (rule-based connector)"))
        else:
            self.stdout.write(self.style.WARNING("  Agent already exists: Steven"))

        if agent.user_id != agent_user.id:
            agent.user = agent_user
            agent.save(update_fields=["user"])
            self.stdout.write(self.style.SUCCESS("  Linked Steven to websocket user"))

        # Attach rule-based connector if agent has none at all
        if agent.connector_id is None:
            agent.connector = rb_connector
            agent.save(update_fields=["connector"])
            self.stdout.write(self.style.SUCCESS("  Attached rule-based connector to Steven"))

        return agent

    def _setup_connector(self, agent):
        from toto.gervazy.models import EncryptedSecret

        api_secret = EncryptedSecret.objects.filter(
            purpose="openai-api-key", state="active"
        ).first()
        if api_secret is None:
            self.stdout.write(
                self.style.WARNING(
                    "  No active Gervazy EncryptedSecret with purpose 'openai-api-key' found. "
                    "Create one in Admin → Gervazy before running live agents."
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
            self.stdout.write(self.style.SUCCESS("  Created connector: OpenAI Chat"))
        else:
            self.stdout.write(self.style.WARNING("  Connector already exists: OpenAI Chat"))

        if agent.connector_id != connector.id:
            agent.connector = connector
            agent.save(update_fields=["connector"])
            self.stdout.write(self.style.SUCCESS("  Attached connector to Steven"))

    def _seed_tools(self, agent):
        for key in ["calculator", "current_time", "echo"]:
            tool, created = AgentTool.objects.get_or_create(
                agent=agent, key=key, defaults={"enabled": True}
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"  Enabled tool: {tool.get_key_display()}"))
            else:
                self.stdout.write(self.style.WARNING(f"  Tool already exists: {tool.get_key_display()}"))
