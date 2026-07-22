from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Seed Vicuna: create default OllamaServer record and sync available models"

    def process(self):
        self.stdout.write(self.style.WARNING("Seeding Vicuna..."))

        from django.conf import settings
        from toto.vicuna.models import OllamaServer

        host = getattr(settings, "VICUNA_OLLAMA_HOST", "http://localhost:11434")

        server, created = OllamaServer.objects.update_or_create(
            host=host,
            defaults={
                "name": "Ollama (default)",
                "is_active": True,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"  Created OllamaServer: {server.name} ({host})"))
        else:
            self.stdout.write(self.style.WARNING(f"  OllamaServer already exists: {server.name} ({host})"))

        try:
            from toto.vicuna.admin import _sync_server
            count = _sync_server(server)
            self.stdout.write(self.style.SUCCESS(f"  Synced {count} model(s) from Ollama."))
        except Exception as exc:
            self.stdout.write(
                self.style.WARNING(
                    f"  Could not sync models from Ollama: {exc}\n"
                    "  Run 'manage.py vicuna_pull_chat' once Ollama is running."
                )
            )

        self.stdout.write(self.style.SUCCESS("Vicuna seeding complete."))
