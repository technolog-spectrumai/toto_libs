from django.contrib.auth import get_user_model

from toto.ingress import IngressCommand
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
        self._ensure_workflows()

        self._seed_tools(agent)
        self.stdout.write(self.style.SUCCESS("Steven seeding complete."))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_default_agent(self):
        from django.conf import settings
        from toto.vicuna.chat import default_ollama_chat_model

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

        ollama_host = getattr(settings, "VICUNA_OLLAMA_HOST", "http://localhost:11434")
        ollama_connector, connector_created = AgentConnector.objects.update_or_create(
            slug="ollama-local",
            defaults={
                "name": "Ollama Local",
                "provider": AgentConnector.OLLAMA,
                "base_url": ollama_host,
                "auth_type": "none",
                "is_active": True,
            },
        )
        if connector_created:
            self.stdout.write(self.style.SUCCESS(f"  Created connector: Ollama Local ({ollama_host})"))
        else:
            self.stdout.write(self.style.WARNING("  Connector already exists: Ollama Local"))

        model = default_ollama_chat_model()
        default_prompt = (
            "You are Steven, a helpful AI agent manager. Be accurate, concise, and safe."
        )

        agent, created = AgentProfile.objects.get_or_create(
            slug=_DEFAULT_SLUG,
            defaults={
                "name": "Steven",
                "description": "A general-purpose AI agent managed by Toto Studio.",
                "user": agent_user,
                "connector": ollama_connector,
                "model_name": model,
                "system_prompt": default_prompt,
                "is_active": True,
            },
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"  Created agent: Steven ({model})"))
        else:
            self.stdout.write(self.style.WARNING("  Agent already exists: Steven"))

        update_fields = []

        if agent.user_id != agent_user.id:
            agent.user = agent_user
            update_fields.append("user")

        if agent.connector_id != ollama_connector.id:
            agent.connector = ollama_connector
            update_fields.append("connector")
            self.stdout.write(self.style.SUCCESS("  Migrated agent to Ollama connector"))

        if update_fields:
            agent.save(update_fields=update_fields)

        return agent

    def _seed_tools(self, agent):
        for key in ["calculator", "current_time", "echo"]:
            tool, created = AgentTool.objects.get_or_create(
                agent=agent, key=key, defaults={"enabled": True}
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"  Enabled tool: {tool.get_key_display()}"))
            else:
                self.stdout.write(self.style.WARNING(f"  Tool already exists: {tool.get_key_display()}"))

    def _ensure_workflows(self):
        from toto.workflows.models import Workflow, WorkflowNode

        wf, created = Workflow.objects.get_or_create(
            slug="steven-run-agent",
            defaults={
                "name": "Steven: Run Agent",
                "description": "Execute a Steven AgentRun via the workflow engine.",
            },
        )
        if created:
            WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Run agent",
                task_name="steven_run_agent",
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.SUCCESS("  Created workflow: Steven: Run Agent"))
        else:
            self.stdout.write(self.style.WARNING("  Workflow already exists: Steven: Run Agent"))
