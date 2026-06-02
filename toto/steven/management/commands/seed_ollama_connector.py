"""Create (or update) the Ollama local AgentConnector. Does not force any agent to use it."""
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Bootstrap the 'ollama-local' AgentConnector for Ollama chat. "
        "Idempotent — safe to run multiple times. "
        "Does not modify existing agents."
    )

    def handle(self, *args, **options):
        from toto.steven.models import AgentConnector

        from toto.vicuna.chat import default_ollama_chat_model

        host = getattr(settings, "VICUNA_OLLAMA_HOST", "http://localhost:11434")
        model = default_ollama_chat_model()

        connector, created = AgentConnector.objects.update_or_create(
            slug="ollama-local",
            defaults={
                "name": "Ollama Local",
                "provider": AgentConnector.OLLAMA,
                "base_url": host,
                "auth_type": AgentConnector.AUTH_NONE,
                "is_active": True,
            },
        )
        action = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} connector: {connector.name!r} "
                f"(provider=ollama, host={host}, model={model})"
            )
        )
        self.stdout.write(
            "Attach this connector to an AgentProfile via Admin → Steven → Agents "
            "to enable Ollama chat for that agent."
        )
