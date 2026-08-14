"""What a call costs, and the one place on this platform that charges afterwards.

## Why this one is charge-after

Everything else here is charge-before-work — ``toto.quota.charge``'s docstring is
explicit that a request arrives, a price is checked, and the work is paid for
before it runs. That is possible because the price is knowable: a compile is a
compile, a render is a render.

A completion is not. Its cost is ``usage.total_tokens``, which nobody knows until
the provider answers. So the sequence here is:

1. **before** — ``check_quota`` and ``check_funds`` for one ``ai.request`` and
   for the WORST CASE in tokens, derived from the provider's own
   ``max_output_tokens``. Somebody who cannot afford the worst case is refused
   up front, with 402, and nothing runs.
2. **run**.
3. **after** — record and charge the REAL token count.

That keeps the property the charge-before rule exists to protect: **nobody
occupies a worker they cannot pay for.** The worst case is an over-estimate by
construction, so the check is conservative in the platform's favour and the
charge is honest in the user's.

4. A **failed call charges nothing at all** — there is no work to pay for, and
   therefore no refund to get wrong. Compare the refund path aralia and texlab
   need, which exists only because they pay first.
"""

from __future__ import annotations

import time

from decimal import Decimal

from django.utils import timezone

from .models import (METRIC_REQUEST, METRIC_TOKENS, AiAgent, AiPersonalization,
                     AiProvider, AiRun, RunStatus)


class NotConfigured(RuntimeError):
    """No active provider, or no key on it. An operator problem, not a user one."""


def store_api_key(provider: AiProvider, raw_key: str, *, actor) -> None:
    """Store a provider's key: encrypt, repoint, retire the old, audit.

    The one sequence, shared by the admin and the settings page so the two
    doors cannot drift: ``store_secret`` → repoint the FK → ``retire_secret``
    on the old row → ``log_secret_event``. Raises ``VaultUnavailable`` for the
    caller to say "the key was NOT changed"; any other failure propagates for
    the caller to format — nothing raised from here ever contains the value.

    The provider row must already be saved (it has a pk): callers save the row
    first, exactly as the admin's ``save_model`` runs ``super()`` first.
    """
    from .vault import vault

    old = provider.secret
    secret = vault.store_secret(
        raw_key,
        name=vault.unique_secret_name(f"steven-{provider.pk}"),
        purpose="ai_api_key",
    )
    provider.secret = secret
    provider.save(update_fields=["secret"])
    vault.retire_secret(old)
    vault.log_secret_event(
        actor, "set_ai_api_key", secret,
        reason=f"set for AI provider #{provider.pk}")


def probe_provider(provider: AiProvider) -> dict:
    """One tiny synchronous completion, to prove a provider BEFORE switching it on.

    Returns ``{"text", "model", "tokens"}`` and nothing else — the answer is
    truncated to 40 characters HERE, so no caller can leak more than that, and
    the key never appears in anything this raises (``client.complete`` keeps it
    out of ``ProviderError`` by contract). Raises ``NotConfigured`` when the
    row has no key, ``VaultUnavailable`` when the vault will not open, and
    ``ProviderError`` when the provider will not answer.
    """
    from .client import complete
    from .vault import vault

    if not provider.secret_id:
        raise NotConfigured("That provider has no API key stored yet.")
    key = vault.read_secret(provider.secret)
    answer = complete(
        base_url=provider.base_url, api_key=key, model=provider.model,
        messages=[{"role": "user", "content": "Reply with the single word: ok"}],
        temperature=0, max_tokens=8, timeout=min(provider.timeout, 30))
    return {
        "text": (answer.get("text") or "").strip()[:40],
        "model": answer.get("model", ""),
        "tokens": (answer.get("usage") or {}).get("total_tokens", "?"),
    }


def active_provider() -> AiProvider:
    provider = AiProvider.current()
    if provider is None:
        raise NotConfigured(
            "No AI provider is switched on. An administrator sets one up in "
            "the admin, under AI providers.")
    if not provider.secret_id:
        raise NotConfigured(
            f"The active provider ({provider.label}) has no API key stored.")
    return provider


def worst_case_units(provider: AiProvider, selection: str,
                     user=None) -> Decimal:
    """The most tokens this call could possibly cost, in thousands.

    A deliberately crude over-estimate: the prompt at four characters per token
    (English prose is nearer four, code is denser, and erring low here would
    under-charge the wallet check), plus the answer ceiling in full. Being
    generous is the safe direction — it can only refuse somebody who is very
    close to empty, and it can never let a call run that cannot be paid for.

    **The system prompt counts too.** It used to be a code constant of known
    size; since the management page it is whatever an operator typed, and a long
    persona is paid for on every single call. Leaving it out would make this
    check quietly optimistic in exactly the case somebody configured it to be
    expensive. The user's own personalization rides the same way — see
    ``_system_chars``.
    """
    prompt_tokens = (Decimal(len(selection or "") + _system_chars(user))
                     / Decimal("4"))
    total = prompt_tokens + Decimal(provider.max_output_tokens)
    return (total / Decimal("1000")).quantize(Decimal("0.001"))


def _system_chars(user=None) -> int:
    """How many characters the configured voice adds to every system message.

    Never raises and never blocks a call: on a host with no agent row, or a
    database that is momentarily unhappy, an assistant that still answers and
    checks against a slightly low estimate is better than one that refuses.

    Counts the asking user's personalization too, when a user is given — it is
    prepended to every one of their calls, so leaving it out would make the
    estimate optimistic for exactly the person who configured it to be long.
    """
    total = len(AiPersonalization.text_for(user)) if user is not None else 0
    try:
        voice = AiAgent.voice()
    except Exception:  # noqa: BLE001
        return total
    if voice is None:
        return total
    notes = voice.kind_notes.values() if voice.kind_notes else ()
    return total + sum(len(part or "") for part in
                       (voice.name, voice.persona, voice.language,
                        voice.house_rules,
                        # The longest note, not the sum: exactly one kind
                        # applies to any given call, and adding all five would
                        # refuse people over tokens that will never be sent.
                        max(notes, key=len, default="")))


def check_affordable(user, provider: AiProvider, selection: str) -> None:
    """Refuse before anything is dispatched. Raises QuotaExceeded / InsufficientFunds.

    Both exceptions carry a ``status_code`` (429 and 402), and both are
    re-exported by ``toto.quota`` with stubs on hosts that do not bill — so this
    function is a no-op there rather than a branch.
    """
    from toto.quota import check_quota
    from toto.quota.charge import check_funds, price_for

    from .models import StevenQuotaPolicy

    units = worst_case_units(provider, selection, user)

    check_quota(StevenQuotaPolicy, METRIC_REQUEST, 1, user)
    check_quota(StevenQuotaPolicy, METRIC_TOKENS, units, user)

    tariff = price_for(user, "steven")
    check_funds(user, tariff, METRIC_REQUEST, 1)
    check_funds(user, tariff, METRIC_TOKENS, units)


def settle(run: AiRun) -> None:
    """Record and charge what the call actually cost. Never raises.

    Called only after a SUCCESSFUL run. Two events and two charges, keyed on the
    run's pk so a retried settle bills once — the same idempotency contract every
    metered app here uses.

    A provider that returns no ``usage`` block charges for the request and
    nothing for tokens. Guessing a token count in order to bill for it would be
    inventing a number and putting it on somebody's bill.
    """
    from toto.quota import record_usage
    from toto.quota.charge import charge, price_for

    from .models import StevenUsageEvent

    user = run.owner
    units = run.billable_units
    source = {"source_type": "steven.AiRun", "source_id": str(run.pk)}

    try:
        record_usage(StevenUsageEvent, METRIC_REQUEST, 1, user, unit="request",
                     source_label=f"{run.surface}/{run.action}",
                     idempotency_key=f"steven.request:{run.pk}", **source)
        if units > 0:
            record_usage(StevenUsageEvent, METRIC_TOKENS, units, user,
                         unit="1k tokens",
                         source_label=f"{run.total_tokens} tokens",
                         idempotency_key=f"steven.tokens:{run.pk}", **source)
    except Exception:  # noqa: BLE001 - metering must not lose a finished answer
        pass

    try:
        tariff = price_for(user, "steven")
        charge(user, tariff, METRIC_REQUEST, 1, unit="request", **source)
        if units > 0:
            charge(user, tariff, METRIC_TOKENS, units, unit="1k tokens", **source)
    except Exception:  # noqa: BLE001
        # The work is done and the answer is the user's. A billing fault is an
        # operator problem, and swallowing an answer to punish it would be the
        # wrong trade — the usage events above are the record either way.
        pass


def execute(run: AiRun) -> AiRun:
    """Do the call and close the run. The worker's entry point.

    Never raises: every outcome is a row somebody can read. The caller
    (``runner.execute_run``) re-raises for the workflow engine's benefit only
    when the run itself failed.
    """
    from .client import ProviderError, complete
    from .surfaces import build_messages, registry, resolve_action
    from .vault import VaultUnavailable, vault

    run.status = RunStatus.RUNNING
    run.save(update_fields=["status"])

    try:
        provider = active_provider()
    except NotConfigured as exc:
        run.finish(status=RunStatus.FAILED, error=str(exc))
        return run

    # Snapshots for the statistics page, assigned on the instance and persisted
    # by finish() whichever way this run ends. Labels, not FKs: the record says
    # what answered AT THE TIME, and renaming an agent must not rewrite it.
    agent = AiAgent.current()
    run.agent_label = agent.name if agent else ""
    run.provider_label = provider.label

    surface = registry.get(run.surface)
    # Same resolver the endpoint used, so a run's meaning cannot drift between
    # being started and being executed.
    action = resolve_action(surface, run.action) if surface else None
    if surface is None or action is None:
        run.finish(status=RunStatus.FAILED,
                   error=f"Unknown surface/action: {run.surface}/{run.action}")
        return run

    try:
        api_key = vault.read_secret(provider.secret)
    except VaultUnavailable as exc:
        run.finish(status=RunStatus.FAILED, error=str(exc))
        return run

    # Read at call time, never cached: an operator who fixes a persona expects
    # the next question to use it, not the next redeploy. The owner's
    # personalization rides the same rule — fetched here, passed as a plain
    # string because toto-base may not import this app's models.
    messages = build_messages(surface, action, selection=run.source_text,
                              instruction=run.instruction,
                              voice=agent.as_voice() if agent else None,
                              personalization=AiPersonalization.text_for(run.owner))

    started = time.monotonic()
    try:
        answer = complete(
            base_url=provider.base_url,
            api_key=api_key,
            model=provider.model,
            messages=messages,
            temperature=provider.temperature,
            max_tokens=provider.max_output_tokens,
            timeout=provider.timeout,
        )
    except ProviderError as exc:
        # A slow failure records its duration too — a timeout that took the
        # whole timeout to happen is a diagnostic fact.
        run.finish(status=RunStatus.FAILED, error=str(exc),
                   duration_ms=int((time.monotonic() - started) * 1000))
        return run

    duration_ms = int((time.monotonic() - started) * 1000)
    text = answer["text"]

    # An answer bound for a document the platform renders is untrusted
    # third-party content, and the antivirus app already owns that judgement.
    # Screened HERE rather than on apply, so a refusal costs the user nothing:
    # the run is failed, and settle below never runs.
    refusal = _screen(surface, text)
    if refusal:
        run.finish(status=RunStatus.FAILED, error=refusal,
                   usage=answer.get("usage"), model_used=answer.get("model", ""),
                   duration_ms=duration_ms)
        return run

    run.finish(status=RunStatus.SUCCESS, result=text,
               usage=answer.get("usage"), model_used=answer.get("model", ""),
               duration_ms=duration_ms)
    settle(run)
    return run


def _screen(surface, text: str) -> str:
    """"" when the answer may be offered, else the reason it may not."""
    if not surface.file_type or not text.strip():
        return ""
    from toto.vault import scanning

    verdict = scanning.scan(text, file_type=surface.file_type,
                            filename=f"assistant.{surface.file_type}")
    if verdict.ok:
        return ""
    return (f"The assistant's answer was refused by the content scanner "
            f"({verdict.reason}: {verdict.detail}). Nothing was applied.")


def run_payload(run: AiRun) -> dict:
    """The polling shape — deliberately the one fileservices and texlab return."""
    return {
        "status": run.status,
        "result": run.result,
        "error": run.error,
        "tokens": run.total_tokens,
        "model": run.model_used,
        "finished": run.is_finished,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "now": timezone.now().isoformat(),
    }
