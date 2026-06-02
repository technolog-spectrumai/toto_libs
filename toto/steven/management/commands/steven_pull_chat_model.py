"""Pull the Ollama chat model and run a smoke test. Does not pull embedding models."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Pull the Steven Ollama chat model (qwen3:4b by default). "
        "Checks GPU availability first. Does NOT pull the embedding model."
    )

    def handle(self, *args, **options):
        from toto.steven.services.ollama_runtime import detect_gpu

        # ── GPU check ────────────────────────────────────────────────────────
        gpu = detect_gpu()
        if gpu["available"]:
            self.stdout.write(f"GPU detected: {gpu['details']}")
        else:
            if getattr(settings, "STEVEN_OLLAMA_REQUIRE_GPU", False):
                self.stderr.write(
                    self.style.ERROR(
                        f"GPU required but not available: {gpu['details']}\n"
                        "Set STEVEN_OLLAMA_REQUIRE_GPU=False to continue on CPU."
                    )
                )
                raise SystemExit(1)
            self.stdout.write(
                self.style.WARNING(
                    f"Warning: no GPU detected ({gpu['details']}). Continuing on CPU — "
                    "inference will be slower."
                )
            )

        host = getattr(settings, "STEVEN_OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        model = getattr(settings, "STEVEN_OLLAMA_CHAT_MODEL", "qwen3:4b")

        # ── Pull ─────────────────────────────────────────────────────────────
        self.stdout.write(f"Pulling chat model {model!r} from {host} …")
        pull_payload = json.dumps({"model": model, "stream": False}).encode()
        pull_req = urllib.request.Request(
            f"{host}/api/pull",
            data=pull_payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(pull_req, timeout=600) as resp:
                pull_data = json.loads(resp.read())
        except urllib.error.URLError as exc:
            self.stderr.write(self.style.ERROR(f"Failed to reach Ollama at {host}: {exc}"))
            raise SystemExit(1)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Pull request failed: {exc}"))
            raise SystemExit(1)

        status = pull_data.get("status", "")
        if status == "success":
            self.stdout.write(self.style.SUCCESS(f"Model {model!r} pulled successfully."))
        else:
            self.stdout.write(f"Ollama pull response: {pull_data}")

        # ── Smoke test ────────────────────────────────────────────────────────
        self.stdout.write("Running smoke test …")
        smoke_payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "Reply with the single word OK."}],
            "stream": False,
        }).encode()
        smoke_req = urllib.request.Request(
            f"{host}/api/chat",
            data=smoke_payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        chat_timeout = getattr(settings, "STEVEN_OLLAMA_CHAT_TIMEOUT", 180)
        try:
            with urllib.request.urlopen(smoke_req, timeout=chat_timeout) as resp:
                smoke_data = json.loads(resp.read())
            reply = (smoke_data.get("message") or {}).get("content", "").strip()
            self.stdout.write(
                self.style.SUCCESS(f"Smoke test passed. Model replied: {reply[:120]!r}")
            )
        except Exception as exc:
            self.stdout.write(
                self.style.WARNING(
                    f"Smoke test failed (model may still be usable): {exc}"
                )
            )
