import json

from django.contrib.auth import get_user_model

from toto.ingress import IngressCommand
from toto.steven.models import AgentConnector, AgentProfile, AgentTool

User = get_user_model()

_DEFAULT_SLUG = "steven-default"

_DEFAULT_RULES = {
    "fallback": (
        "I'm not sure I understood. I can help with: pricing, support, "
        "accounts, cancellations, or platform questions. Type 'help' for built-in commands."
    ),
    "rules": [
        {
            "intent": "greeting",
            "required_any": ["hello", "hi", "hey", "greet", "morning", "evening", "good"],
            "required_all": [],
            "response": "Hello! How can I help you today?",
        },
        {
            "intent": "farewell",
            "required_any": ["bye", "goodbye", "ciao", "later", "farewell", "see"],
            "required_all": [],
            "response": "Goodbye! Feel free to come back anytime.",
        },
        {
            "intent": "pricing",
            "required_any": ["price", "cost", "fee", "plan", "tier", "billing", "pay", "payment", "charge"],
            "required_all": [],
            "response": "Which plan are you asking about? We have free, starter, and pro tiers.",
        },
        {
            "intent": "cancel_subscription",
            "required_any": ["cancel", "stop", "unsubscribe", "quit", "terminate"],
            "required_all": ["subscription"],
            "response": "I can help with cancellation. Would you like to cancel your subscription? Please confirm.",
        },
        {
            "intent": "technical_support",
            "required_any": ["error", "bug", "broken", "issue", "problem", "fix", "crash", "fail", "wrong", "not work"],
            "required_all": [],
            "response": "Can you describe what is not working? I'll log it and escalate to the team.",
        },
        {
            "intent": "account",
            "required_any": ["account", "profile", "login", "password", "username", "sign", "register", "access"],
            "required_all": [],
            "response": "For account-related questions, go to your profile settings or contact an admin.",
        },
        {
            "intent": "feature_request",
            "required_any": ["feature", "request", "suggest", "idea", "wish", "roadmap", "add"],
            "required_all": [],
            "response": "Thanks for the suggestion! Feature requests can be submitted via the feedback form.",
        },
        {
            "intent": "contact",
            "required_any": ["contact", "reach", "email", "phone", "talk", "speak", "human"],
            "required_all": [],
            "response": "To reach a human, please use the contact form or email support@example.com.",
        },
    ],
}


class Command(IngressCommand):
    help = "Seed Steven with a starter agent and default tools"

    def process(self):
        self.stdout.write(self.style.WARNING("Seeding Steven..."))
        self._check_tariff()

        # Always create the default agent so the floating widget works
        # even without FULL_INGRESS (it falls back to stub mode without a connector).
        agent = self._ensure_default_agent()
        self._ensure_workflows()

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

        default_prompt = json.dumps(_DEFAULT_RULES, indent=2)

        agent, created = AgentProfile.objects.get_or_create(
            slug=_DEFAULT_SLUG,
            defaults={
                "name": "Steven",
                "description": "A general-purpose AI agent managed by Toto Studio.",
                "user": agent_user,
                "connector": rb_connector,
                "model_name": "LoboLightNLP",
                "system_prompt": default_prompt,
                "is_active": True,
            },
        )

        if created:
            self.stdout.write(self.style.SUCCESS("  Created agent: Steven (LoboLightNLP rule-based)"))
        else:
            self.stdout.write(self.style.WARNING("  Agent already exists: Steven"))

        update_fields = []

        # Upgrade plain-text system_prompt to JSON rule set
        try:
            json.loads(agent.system_prompt)
        except (json.JSONDecodeError, TypeError):
            agent.system_prompt = default_prompt
            update_fields.append("system_prompt")
            self.stdout.write(self.style.SUCCESS("  Upgraded system_prompt to JSON rule set"))

        # Fix model name for rule-based agents
        if agent.model_name != "LoboLightNLP" and agent.connector_id == rb_connector.id:
            agent.model_name = "LoboLightNLP"
            update_fields.append("model_name")
            self.stdout.write(self.style.SUCCESS("  Set model_name to LoboLightNLP"))

        if update_fields:
            agent.save(update_fields=update_fields)

        if agent.user_id != agent_user.id:
            agent.user = agent_user
            agent.save(update_fields=["user"])
            self.stdout.write(self.style.SUCCESS("  Linked Steven to websocket user"))

        # Use rule-based connector when agent has no connector, or has OpenAI without a key
        needs_rb = (
            agent.connector_id is None
            or (
                agent.connector is not None
                and agent.connector.provider == "openai"
                and agent.connector.api_secret_id is None
            )
        )
        if needs_rb and agent.connector_id != rb_connector.id:
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

    def _check_tariff(self):
        from django.apps import apps as django_apps
        if not django_apps.is_installed("toto.tariffs"):
            self.stdout.write("  toto.tariffs not installed — skipping tariff check.")
            return
        from toto.tariffs.models import Tariff
        if Tariff.objects.filter(code="AI-INFERENCE").exists():
            self.stdout.write("  [steven] AI-INFERENCE tariff: ready.")
        else:
            self.stdout.write(self.style.WARNING(
                "  [steven] AI-INFERENCE tariff not found — run ingress_tariffs first."
            ))

