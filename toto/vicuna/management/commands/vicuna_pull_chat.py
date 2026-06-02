"""Pull Ollama chat model(s) and run a smoke test."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Pull Vicuna Ollama chat model(s). "
        "Checks GPU availability first. Does NOT pull the embedding model."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--model",
            type=str,
            default=None,
            metavar="MODEL",
            help=(
                "Pull a specific model. Must be one of the allowed choices "
                "(qwen3:0.6b, qwen3:1.7b, qwen3:4b). "
                "Defaults to the configured VICUNA_CHAT_MODEL."
            ),
        )
        parser.add_argument(
            "--all",
            action="store_true",
            default=False,
            help="Pull all allowed Ollama chat models.",
        )

    def handle(self, *args, **options):
        from toto.vicuna.chat import default_ollama_chat_model, ollama_chat_model_choices
        from toto.vicuna.runtime import detect_gpu

        choices = ollama_chat_model_choices()

        if options["all"]:
            models_to_pull = list(choices)
        elif options["model"]:
            requested = options["model"]
            if requested not in choices:
                raise CommandError(
                    f"Model {requested!r} is not an allowed choice. "
                    f"Allowed: {', '.join(choices)}"
                )
            models_to_pull = [requested]
        else:
            models_to_pull = [default_ollama_chat_model()]

        gpu = detect_gpu()
        if gpu["available"]:
            self.stdout.write(f"GPU detected: {gpu['details']}")
        else:
            if getattr(settings, "VICUNA_REQUIRE_GPU", False):
                self.stderr.write(
                    self.style.ERROR(
                        f"GPU required but not available: {gpu['details']}\n"
                        "Set VICUNA_REQUIRE_GPU=False to continue on CPU."
                    )
                )
                raise SystemExit(1)
            self.stdout.write(
                self.style.WARNING(
                    f"Warning: no GPU detected ({gpu['details']}). "
                    "Continuing on CPU — inference will be slower."
                )
            )

        host = getattr(settings, "VICUNA_OLLAMA_HOST", "http://localhost:11434").rstrip("/")

        for model in models_to_pull:
            self._pull_model(host, model)

        smoke_model = models_to_pull[0] if len(models_to_pull) == 1 else default_ollama_chat_model()
        self._smoke_test(host, smoke_model)

    def _pull_model(self, host: str, model: str) -> None:
        self.stdout.write(f"Pulling {model!r} …")
        payload = json.dumps({"model": model, "stream": False}).encode()
        req = urllib.request.Request(
            f"{host}/api/pull",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read())
        except urllib.error.URLError as exc:
            self.stderr.write(self.style.ERROR(f"Failed to reach Ollama at {host}: {exc}"))
            raise SystemExit(1)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Pull request failed for {model!r}: {exc}"))
            raise SystemExit(1)

        if data.get("status") == "success":
            self.stdout.write(self.style.SUCCESS(f"  {model!r} ready."))
        else:
            self.stdout.write(f"  Ollama response for {model!r}: {data}")

    def _smoke_test(self, host: str, model: str) -> None:
        self.stdout.write(f"Running smoke test with {model!r} …")
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "Reply with the single word OK."}],
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"{host}/api/chat",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        timeout = getattr(settings, "VICUNA_CHAT_TIMEOUT", 180)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
            reply = (data.get("message") or {}).get("content", "").strip()
            self.stdout.write(
                self.style.SUCCESS(f"Smoke test passed. {model!r} replied: {reply[:120]!r}")
            )
        except Exception as exc:
            self.stdout.write(
                self.style.WARNING(f"Smoke test failed (model may still be usable): {exc}")
            )
