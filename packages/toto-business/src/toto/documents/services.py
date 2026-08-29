"""Handing a built document to aralia, and reporting back.

One function. It exists so the three export views do not each repeat the
create-meter-dispatch dance, and so there is exactly one place that knows the
Business Center renders through aralia rather than through anything of its own.

**The dependency is SOFT, and it has to be.** This wheel installs on any host;
`toto.aralia` is host-owned and not every host carries it. So every aralia
import lives behind `available()`, exactly the way
`toto_libs/limbo/hesperis/integration/ledger.py` treats this package's own ledger — a wheel
that hard-imported a host app would refuse to install anywhere else. Where
the renderer is absent, `export` refuses with a message instead of rendering.
"""

from __future__ import annotations

from django.apps import apps

# toto.quota ships in toto-base, which this package hard-depends on, so these
# stay at module scope: only the ARALIA imports below are soft, and keeping
# these here also keeps `refund_for` patchable where the tests expect it.
from toto.quota.api import InArrears, QuotaExceeded, check_quota, record_usage
from toto.quota.charge import (
    InsufficientFunds,
    charge,
    check_funds,
    price_for,
    refund_for,
)

SOURCE_TYPE = "aralia.AraliaRun"


class ExportRefused(Exception):
    """The render could not be queued, and the message says why."""


def available() -> bool:
    """Is there a renderer on this host at all?"""
    return apps.is_installed("toto.aralia")


def export(html: str, *, user, label: str = "export"):
    """Queue one Business Center export. Returns the AraliaRun.

    The metering order is aralia's own, for the same reasons: quota and funds
    BEFORE the row exists, the charge following the usage event rather than the
    request, and a refund when the dispatch never happened.
    """
    if not available():
        raise ExportRefused(
            "This host has no PDF renderer — the Business Center exports "
            "through toto.aralia, which is not installed here.")
    from toto.aralia import dispatch
    from toto.aralia.metrics import METRIC
    from toto.aralia.models import AraliaQuotaPolicy, AraliaUsageEvent

    if not (html or "").strip():
        raise ExportRefused("There is nothing to render.")

    tariff = price_for(user, "aralia")
    try:
        check_quota(AraliaQuotaPolicy, METRIC, 1, user)
        check_funds(user, tariff, METRIC, 1)
    except (QuotaExceeded, InArrears, InsufficientFunds) as exc:
        raise ExportRefused(str(exc)) from None

    run = dispatch.create_run(html=html, user=user)
    source = {"source_type": SOURCE_TYPE, "source_id": str(run.pk),
              "source_label": label}
    if record_usage(AraliaUsageEvent, METRIC, 1, user,
                    idempotency_key=f"{METRIC}:{run.pk}", **source) is not None:
        charge(user, tariff, METRIC, 1, **source)

    try:
        dispatch.dispatch_run(run)
    except dispatch.CannotQueue as exc:
        dispatch.fail_run(run, str(exc))
        refund_for(SOURCE_TYPE, str(run.pk), METRIC,
                   reason="the export was never queued")
        raise ExportRefused(str(exc)) from None
    return run
