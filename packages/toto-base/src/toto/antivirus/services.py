"""Affordability, execution and settlement for on-demand scans.

The order is the platform's charge-before-work rule, adapted the same way
steven adapted it: quota and funds are CHECKED before anything is queued —
nobody occupies a worker they cannot pay for — and the charge lands AFTER a
delivered verdict. A scan's price is knowable in advance, but paying first
would buy the refund machinery aralia and texlab need for the one failure mode
a scan actually has (an unreadable file), and a failed run that was never
billed needs no unwinding at all.

A REFUSED verdict is a SUCCESSFUL run and it charges: the scan ran and the
answer is "this file is hostile" — that is the service, delivered. Only a run
that produced no verdict is free.
"""

from __future__ import annotations

from django.utils import timezone

from toto.vault.scanning import scan as facade_scan

from . import engine
from .models import METRIC_SCAN, RunStatus, ScanRun


def check_affordable(user) -> None:
    """Refuse before dispatch. Raises QuotaExceeded / InsufficientFunds.

    Both carry ``status_code`` (429 / 402) and both are no-op stubs on hosts
    that do not bill, so this is not a branch there.
    """
    from toto.quota import check_quota
    from toto.quota.charge import check_funds, price_for

    from .models import AntivirusQuotaPolicy

    check_quota(AntivirusQuotaPolicy, METRIC_SCAN, 1, user)
    check_funds(user, price_for(user, "antivirus"), METRIC_SCAN, 1)


def settle(run: ScanRun) -> None:
    """Record and charge one delivered scan. Never raises.

    Keyed on the run's pk, so a retried settle bills once — the idempotency
    contract every metered app here keeps.
    """
    from toto.quota import record_usage
    from toto.quota.charge import charge, price_for

    from .models import AntivirusUsageEvent

    source = {"source_type": "antivirus.ScanRun", "source_id": str(run.pk),
              "source_label": run.file.title}
    try:
        record_usage(AntivirusUsageEvent, METRIC_SCAN, 1, run.owner,
                     unit="scan", idempotency_key=f"antivirus.scan:{run.pk}",
                     **source)
    except Exception:  # noqa: BLE001 - metering must not lose a verdict
        pass
    try:
        charge(run.owner, price_for(run.owner, "antivirus"), METRIC_SCAN, 1,
               unit="scan", **source)
    except Exception:  # noqa: BLE001 - the verdict is delivered either way
        pass


def execute(run: ScanRun) -> ScanRun:
    """Do the scan and close the run. The worker's entry point. Never raises."""
    run.status = RunStatus.RUNNING
    run.save(update_fields=["status"])

    vault_file = run.file
    try:
        with vault_file.file.open("rb") as handle:
            content = handle.read()
    except (OSError, ValueError) as exc:
        # Recorded, not swallowed: a file that cannot be checked is a security
        # fact of its own kind. The run FAILS and charges nothing — there is
        # no verdict to pay for.
        row = engine.record_failure(vault_file, str(exc), user=run.owner,
                                    door="manual")
        run.finish(status=RunStatus.FAILED,
                   error="The file could not be read.", result=row)
        return run

    verdict = facade_scan(content, file_type=vault_file.file_type,
                          filename=vault_file.title)
    row = engine.record(vault_file, verdict, user=run.owner, door="manual",
                        content=content)
    run.finish(status=RunStatus.SUCCESS, result=row)
    settle(run)
    return run


def run_payload(run: ScanRun) -> dict:
    """The polling shape — the same idiom fileservices, texlab and steven use."""
    result = run.result
    payload = {
        "status": run.status,
        "finished": run.is_finished,
        "error": run.error,
        "ok": run.status != RunStatus.FAILED,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "now": timezone.now().isoformat(),
    }
    if result is not None and run.status == RunStatus.SUCCESS:
        payload.update({
            "clean": not result.is_threat and result.verdict != "error",
            "reason": result.reason,
            "detail": result.detail,
            "line": result.line,
        })
    return payload
