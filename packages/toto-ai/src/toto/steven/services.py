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

from decimal import Decimal

from django.utils import timezone

from .models import (METRIC_REQUEST, METRIC_TOKENS, AiAgent, AiProvider, AiRun,
                     RunStatus)


class NotConfigured(RuntimeError):
    """No active provider, or no key on it. An operator problem, not a user one."""


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


def worst_case_units(provider: AiProvider, selection: str) -> Decimal:
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
    expensive.
    """
    prompt_tokens = Decimal(len(selection or "") + _system_chars()) / Decimal("4")
    total = prompt_tokens + Decimal(provider.max_output_tokens)
    return (total / Decimal("1000")).quantize(Decimal("0.001"))


def _system_chars() -> int:
    """How many characters the configured voice adds to every system message.

    Never raises and never blocks a call: on a host with no agent row, or a
    database that is momentarily unhappy, an assistant that still answers and
    checks against a slightly low estimate is better than one that refuses.
    """
    try:
        voice = AiAgent.voice()
    except Exception:  # noqa: BLE001
        return 0
    if voice is None:
        return 0
    notes = voice.kind_notes.values() if voice.kind_notes else ()
    return sum(len(part or "") for part in
               (voice.name, voice.persona, voice.language, voice.house_rules,
                # The longest note, not the sum: exactly one kind applies to any
                # given call, and adding all five would refuse people over
                # tokens that will never be sent.
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

    units = worst_case_units(provider, selection)

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
    # the next question to use it, not the next redeploy.
    messages = build_messages(surface, action, selection=run.source_text,
                              instruction=run.instruction,
                              voice=AiAgent.voice())

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
        run.finish(status=RunStatus.FAILED, error=str(exc))
        return run

    text = answer["text"]

    # An answer bound for a document the platform renders is untrusted
    # third-party content, and the antivirus app already owns that judgement.
    # Screened HERE rather than on apply, so a refusal costs the user nothing:
    # the run is failed, and settle below never runs.
    refusal = _screen(surface, text)
    if refusal:
        run.finish(status=RunStatus.FAILED, error=refusal,
                   usage=answer.get("usage"), model_used=answer.get("model", ""))
        return run

    run.finish(status=RunStatus.SUCCESS, result=text,
               usage=answer.get("usage"), model_used=answer.get("model", ""))
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
