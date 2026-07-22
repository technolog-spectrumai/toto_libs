"""Seed Sabbia agents.

The OpenAI "Steven" agent is seeded whenever ``SABBIA_OPENAI`` is on — in both full
and non-full ingress. An Ollama agent is seeded when ``SABBIA_OLLAMA`` is on. The
active platform's PlatformChatbot is pointed at Steven (or the Ollama agent if
OpenAI is off).
"""
from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Seed Sabbia agents (Steven/OpenAI always when SABBIA_OPENAI is on)."

    def process(self):
        from django.conf import settings

        self.stdout.write(self.style.WARNING("Seeding Sabbia..."))

        if getattr(settings, "SABBIA_OPENAI", False):
            self._ensure_vault()
            self._seed_openai_steven()
        else:
            self.stdout.write(self.style.WARNING(
                "  SABBIA_OPENAI is off — skipping the Steven (OpenAI) agent."
            ))

        if getattr(settings, "SABBIA_OLLAMA", False):
            self._seed_ollama_agent()

        self._link_platform_chatbot()
        self.stdout.write(self.style.SUCCESS("Sabbia seeding complete."))

    def _ensure_vault(self):
        """Create the sabbia credential strongbox (idempotent) so the OpenAI key
        can be stored in the same pass — no separate manual `sabbia_init_vault`.

        No-op with a clear warning when SABBIA_VAULT_PASSWORD is unset: Steven is
        then seeded keyless and the key can be added later once the password is
        configured.
        """
        from django.conf import settings
        from django.core.management import call_command
        from django.core.management.base import CommandError

        if not (getattr(settings, "SABBIA_VAULT_PASSWORD", "") or "").strip():
            self.stdout.write(self.style.WARNING(
                "  SABBIA_VAULT_PASSWORD not set — skipping vault init "
                "(the OpenAI key will not be stored)."
            ))
            return
        try:
            call_command("sabbia_init_vault", verbosity=0)
            self.stdout.write(self.style.SUCCESS("  Sabbia vault ready."))
        except CommandError as exc:
            self.stdout.write(self.style.WARNING(f"  Vault init failed: {exc}"))

    def _seed_openai_steven(self):
        from django.conf import settings

        from toto.sabbia import vault
        from toto.sabbia.models import Agent, AgentConnector

        connector, _ = AgentConnector.objects.get_or_create(
            slug="sabbia-openai",
            defaults={
                "name": "Sabbia OpenAI",
                "provider": AgentConnector.PROVIDER_OPENAI,
                "base_url": "https://api.openai.com/v1/",
                "auth_type": AgentConnector.AUTH_BEARER_TOKEN,
                "is_active": True,
            },
        )

        # Store the OpenAI key in the gervazy vault if provided and not already set.
        api_key = (getattr(settings, "OPENAI_API_KEY", "") or "").strip()
        if api_key and connector.api_secret_id is None:
            try:
                secret = vault.store_secret(
                    api_key, name="sabbia-openai", purpose="sabbia OpenAI key"
                )
                connector.api_secret = secret
                connector.save(update_fields=["api_secret"])
                self.stdout.write(self.style.SUCCESS("  Stored OpenAI key in sabbia vault."))
            except Exception as exc:
                self.stdout.write(self.style.WARNING(
                    f"  Could not store OpenAI key (run sabbia_init_vault first): {exc}"
                ))
        elif not api_key and connector.api_secret_id is None:
            self.stdout.write(self.style.WARNING(
                "  OPENAI_API_KEY not set — Steven seeded without a key "
                "(configure later via admin)."
            ))

        # GraphRAG: enabled only when the graph layer is actually built, so Steven
        # is correct in both build profiles. Toggle/knobs live in endpoint_config
        # (no migration) — see toto.ravioli.rag.
        rag_enabled = bool(getattr(settings, "RAVIOLI_ENABLED", False))
        _, created = Agent.objects.update_or_create(
            slug="steven",
            defaults={
                "name": "Steven",
                "description": "OpenAI ChatGPT-backed assistant.",
                "endpoint_type": Agent.ENDPOINT_OPENAI,
                "connector": connector,
                "model_name": "gpt-4.1-mini",
                "system_prompt": "You are Steven, a helpful, concise assistant.",
                "is_active": True,
                "endpoint_config": {
                    "rag": {"enabled": rag_enabled, "top_k": 5, "text2cypher": True}
                },
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"  {'Created' if created else 'Updated'} agent: Steven (openai)"
        ))

    def _seed_ollama_agent(self):
        from toto.sabbia.models import Agent

        try:
            from toto.vicuna.chat import default_ollama_chat_model

            model_name = default_ollama_chat_model()
        except Exception:
            model_name = ""

        _, created = Agent.objects.update_or_create(
            slug="ollama-local",
            defaults={
                "name": "Ollama Local",
                "description": "Local Ollama agent via Vicuna.",
                "endpoint_type": Agent.ENDPOINT_OLLAMA,
                "model_name": model_name,
                "system_prompt": "You are a helpful, concise assistant.",
                "is_active": True,
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"  {'Created' if created else 'Updated'} agent: Ollama Local (ollama)"
        ))

    def _link_platform_chatbot(self):
        from toto.core.models import Platform

        from toto.sabbia.models import Agent, PlatformChatbot

        platform = Platform.objects.first()
        if platform is None:
            self.stdout.write(self.style.WARNING(
                "  No Platform found — skipping PlatformChatbot link."
            ))
            return

        agent = (
            Agent.objects.filter(slug="steven", is_active=True).first()
            or Agent.objects.filter(is_active=True).order_by("name").first()
        )
        if agent is None:
            self.stdout.write(self.style.WARNING(
                "  No active agent — skipping PlatformChatbot link."
            ))
            return

        _, created = PlatformChatbot.objects.update_or_create(
            platform=platform,
            defaults={"agent": agent, "is_enabled": True},
        )
        self.stdout.write(self.style.SUCCESS(
            f"  {'Created' if created else 'Updated'} PlatformChatbot: "
            f"{platform} → {agent.name}"
        ))
