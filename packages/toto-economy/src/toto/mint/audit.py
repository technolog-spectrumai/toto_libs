"""Remote ledger audit: ask a branch for its books and check them.

Triggered by staff on the master, on demand. It is the same statement the
top-up check consumes, so there is one format and one verifier rather than two
that must be kept in step.

**Nothing here punishes anything.** A failed audit is recorded and flagged.
Suspending a platform's contract or refusing its next top-up is an
administrator's decision, because a discrepancy is at least as likely to be a
bug in our own arithmetic as it is a compromised branch — and taking a whole
platform offline on that basis is not a thing a background job should do.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("toto.mint.audit")

#: How long to wait for a branch. Short: this runs from a staff page, and a
#: slow branch must not hold a web worker.
TIMEOUT = (3, 10)


def fetch_attestation(base_url: str) -> tuple[dict | None, str]:
    """GET the branch's signed statement. Returns (envelope, error)."""
    import requests

    url = base_url.rstrip("/") + "/assets/attestation.json"
    try:
        response = requests.get(url, timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - unreachable is an outcome
        return None, f"{type(exc).__name__}: {exc}"
    if response.status_code != 200:
        return None, f"HTTP {response.status_code}"
    try:
        return response.json(), ""
    except ValueError:
        # A captive portal or a proxy error page, not a branch.
        return None, "the response was not JSON"


def audit_branch(*, node: str, base_url: str, actor=None):
    """Pull, verify, record. Returns the LedgerAudit row."""
    from toto.assets.allocation import position_account
    from toto.assets.models import AssetHolding, CurrencyContract
    from toto.assets.statement import (DISCREPANCY, OK, UNREACHABLE,
                                       UNVERIFIABLE, verify_statement)

    from .models import LedgerAudit

    contract = (CurrencyContract.objects.filter(
        node_id=node, superseded_at__isnull=True)
        .select_related("asset").first())
    if contract is None:
        return LedgerAudit.objects.create(
            node_id=node, actor=actor, outcome=UNVERIFIABLE,
            findings=["This platform has no active contract here, so there is "
                      "nothing to check a statement against."])

    envelope, error = fetch_attestation(base_url)
    if envelope is None:
        # "I could not check" is NOT "I checked and it is fine". Keeping these
        # apart is the whole reason there are four outcomes.
        return LedgerAudit.objects.create(
            node_id=node, actor=actor, outcome=UNREACHABLE,
            findings=[f"Could not reach {base_url}: {error}"])

    holding = AssetHolding.objects.filter(
        asset=contract.asset,
        account=position_account(node=node, asset=contract.asset)).first()
    allocated = holding.balance_base_units if holding else 0

    result = verify_statement(
        envelope, expected_node=node, expected_allocated=allocated,
        expected_currency_hash=contract.currency_hash,
        expected_serial=contract.serial)

    row = LedgerAudit.objects.create(
        node_id=node, actor=actor, outcome=result["outcome"],
        findings=result["findings"], statement=result.get("statement") or {},
        expected_allocated_base_units=allocated)
    if result["outcome"] != OK:
        log.warning("ledger audit for %s: %s — %s", node, result["outcome"],
                    json.dumps(result["findings"]))
    return row
