from importlib.util import find_spec

from django.conf import settings
from django.core.management.base import BaseCommand

from toto.steven.models import AgentConnector, AgentProfile, AgentTool


class Command(BaseCommand):
    help = "Create a starter Steven agent. Uses Ollama if langchain-ollama is installed, otherwise OpenAI."

    def handle(self, *args, **options):
        if find_spec("langchain_ollama") is not None:
            connector = self._ensure_ollama_connector()
            model_name = getattr(settings, "VICUNA_CHAT_MODEL", "qwen3:4b")
        else:
            connector = self._ensure_openai_connector()
            model_name = "openai:gpt-4.1-mini"

        agent, created = AgentProfile.objects.get_or_create(
            slug="steven-default",
            defaults={
                "name": "Steven",
                "description": "A general-purpose AI agent managed by Toto Studio.",
                "connector": connector,
                "model_name": model_name,
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
            self.style.SUCCESS(
                f'{"Created" if created else "Updated"} {agent.name} '
                f"(provider={connector.provider}, model={model_name})"
            )
        )

    def _ensure_ollama_connector(self):
        host = getattr(settings, "VICUNA_OLLAMA_HOST", "http://localhost:11434")
        connector, _ = AgentConnector.objects.update_or_create(
            slug="ollama-local",
            defaults={
                "name": "Ollama Local",
                "provider": AgentConnector.OLLAMA,
                "base_url": host,
                "auth_type": AgentConnector.AUTH_NONE,
                "is_active": True,
            },
        )
        return connector

    def _ensure_openai_connector(self):
        from toto.gervazy.models import EncryptedSecret

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
        return connector
