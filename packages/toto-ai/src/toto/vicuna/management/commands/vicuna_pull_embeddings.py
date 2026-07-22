"""Pull the configured Vicuna embedding model from Ollama."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Pull the Vicuna embedding model from Ollama. "
        "Does not require Neo4j. Does not pull chat models."
    )

    def handle(self, *args, **options):
        host = getattr(settings, "VICUNA_OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        model = getattr(settings, "VICUNA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")

        self.stdout.write(f"Pulling embedding model {model!r} from {host} …")

        payload = json.dumps({"model": model, "stream": False}).encode()
        req = urllib.request.Request(
            f"{host}/api/pull",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read())
        except urllib.error.URLError as exc:
            self.stderr.write(
                self.style.ERROR(f"Failed to reach Ollama at {host}: {exc}")
            )
            raise SystemExit(1)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Pull request failed: {exc}"))
            raise SystemExit(1)

        status = data.get("status", "")
        if status == "success":
            self.stdout.write(self.style.SUCCESS(f"Model {model!r} is ready."))
        else:
            self.stdout.write(f"Ollama response: {data}")
            self.stdout.write(self.style.SUCCESS(f"Model {model!r} pull complete."))
