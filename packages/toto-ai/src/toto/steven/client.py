"""The OpenAI call. One function, `urllib`, no SDK.

Lifted from `toto.sabbia.endpoints.openai` — 48 lines that have worked and been
tested — with the two things it does not do:

* send `max_tokens`, so a runaway answer has a ceiling; and
* **return `usage`**, which is the entire basis of what this platform charges.
  A completion whose token count is thrown away cannot be billed honestly, and
  billing per call regardless of size was the alternative we rejected.

Deliberately no `openai` package. The wire format is a POST with a JSON body; a
dependency would buy retries and typed errors and cost a pin on every host that
installs the assistant.
"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ProviderError(RuntimeError):
    """The provider refused, timed out, or answered something unusable."""


def complete(*, base_url: str, api_key: str, model: str, messages: list,
             temperature=0.2, max_tokens: int = 1024, timeout: int = 60) -> dict:
    """One chat completion. Returns ``{"text": str, "usage": dict, "model": str}``.

    Every failure is a ``ProviderError`` carrying a sentence an operator can act
    on. The API key is NEVER part of that sentence — an error page is exactly
    where a leaked credential goes unnoticed.
    """
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
    }).encode("utf-8")

    request = Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "toto-steven/1.0",
        },
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
    except HTTPError as exc:
        # The body often names the real problem (bad key, unknown model, rate
        # limit). Read it, cap it, and never echo the request.
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except Exception:  # noqa: BLE001
            pass
        raise ProviderError(f"The provider answered {exc.code}. {detail}".strip()) from exc
    except URLError as exc:
        raise ProviderError(f"Could not reach the provider: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ProviderError(f"The provider did not answer within {timeout}s.") from exc
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"{type(exc).__name__}: {exc}") from exc

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("The provider's answer had no message content.") from exc

    return {
        "text": text or "",
        # Absent on some compatible endpoints. An empty dict means "we do not
        # know how big this was", and services.settle charges nothing for it
        # rather than guessing.
        "usage": data.get("usage") or {},
        "model": data.get("model") or model,
    }
