"""Internal HTTP chat endpoint for Vicuna.

Proxies a chat request to the local Ollama server (`/api/chat`). Consumed
server-to-server by sabbia's Ollama endpoint type — not a browser-facing view.
"""
import json
from urllib.request import Request, urlopen

from django.conf import settings
from django.http import HttpResponseBadRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from toto.vicuna.chat import ollama_host, resolve_ollama_chat_model


class _ModelProfile:
    """Minimal shim so resolve_ollama_chat_model can read ``.model_name``."""

    def __init__(self, model_name):
        self.model_name = model_name or ""


@csrf_exempt
@require_POST
def chat(request):
    expected = getattr(settings, "VICUNA_INTERNAL_TOKEN", "")
    if expected and request.headers.get("X-Internal-Token") != expected:
        return JsonResponse({"error": "forbidden"}, status=403)

    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("invalid json")

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return HttpResponseBadRequest("messages required")

    # Clamp the model to the configured allow-list (falls back to the default).
    model = resolve_ollama_chat_model(_ModelProfile(body.get("model")))
    temperature = float(
        body.get("temperature", getattr(settings, "VICUNA_CHAT_TEMPERATURE", 0.1))
    )

    host = ollama_host().rstrip("/")
    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
    ).encode("utf-8")
    req = Request(
        f"{host}/api/chat",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    timeout = int(getattr(settings, "VICUNA_CHAT_TIMEOUT", 180))
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        return JsonResponse({"error": f"ollama unreachable: {exc}"}, status=502)

    reply = (data.get("message") or {}).get("content", "")
    return JsonResponse({"reply": reply, "model": model})
