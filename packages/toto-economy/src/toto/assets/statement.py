"""What a branch says about its own books, and how the master checks it.

The anti-cheat, and it is deliberately small: no background protocol, nothing
to schedule, retry or monitor. A branch presents a statement when it wants
MORE money — which is the moment someone is already paying attention — and the
master checks the arithmetic against what it actually allocated.

An honest branch cannot over-draw in the first place: its reserve is an
ordinary ledger balance and double-entry will not let it spend past it. So
this catches a TAMPERED or compromised branch, which is exactly what it is for.

The statement is signed with the platform key, not the issuer key. A branch can
attest to its own books; it can never create money. Different keys, different
context strings.
"""

from __future__ import annotations

import hashlib
import json
import struct

STATEMENT_CONTEXT = b"toto/currency/v1/statement"

OK = "ok"
DISCREPANCY = "discrepancy"
UNVERIFIABLE = "unverifiable"
UNREACHABLE = "unreachable"


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def framed(body: bytes) -> bytes:
    return STATEMENT_CONTEXT + b"\x00" + struct.pack(">I", len(body)) + body


def build_statement() -> dict:
    """The branch's account of itself. No user PII — account codes only."""
    from .allocation import allocated_base_units, drawn_base_units
    from .contracts import local_contract, node_id
    from .models import LedgerHash

    contract = local_contract()
    if contract is None:
        return {"v": 1, "node_id": node_id(), "contract_serial": 0,
                "currency_hash": "", "allocated_base_units": 0,
                "drawn_base_units": 0, "chain_head": ""}

    asset = contract.asset
    head = LedgerHash.objects.order_by("-id").first()
    return {
        "v": 1,
        "node_id": node_id(),
        "contract_serial": contract.serial,
        "currency_hash": asset.currency_hash,
        "allocated_base_units": allocated_base_units(asset=asset),
        "drawn_base_units": drawn_base_units(asset=asset),
        "chain_head": head.hash if head else "",
    }


def sign_statement(statement: dict) -> dict:
    """Wrap a statement with this platform's signature."""
    from .models import PlatformKey

    key = PlatformKey.objects.ensure_local()
    return {
        "statement": statement,
        "signature": key.sign(framed(canonical(statement))),
        "platform_key_fingerprint": key.fingerprint,
        "platform_key_pem": key.public_key_pem,
    }


def verify_statement(envelope: dict, *, expected_node: str,
                     expected_allocated: int, expected_currency_hash: str,
                     expected_serial: int, pinned_key_pem: str = "") -> dict:
    """Master side. Returns {outcome, findings} and changes nothing.

    Four outcomes, and the last two are kept apart on purpose: "I could not
    check" is not "I checked and it is fine". Conflating them is how an
    unreachable branch quietly becomes a passing one.

    No automatic punishment is applied here or anywhere else. A discrepancy is
    at least as likely to be our own bug as a compromise, and taking a whole
    platform offline on an arithmetic mismatch is an administrator's decision.
    """
    from .models import PlatformKey

    findings: list[str] = []
    statement = envelope.get("statement") or {}

    pem = pinned_key_pem or envelope.get("platform_key_pem", "")
    checker = PlatformKey(public_key_pem=pem)
    if not pem or not checker.verify(framed(canonical(statement)),
                                     envelope.get("signature", "")):
        return {"outcome": UNVERIFIABLE,
                "findings": ["The statement's signature does not verify."],
                "statement": statement}

    if statement.get("node_id") != expected_node:
        findings.append(
            f"Statement is from “{statement.get('node_id')}”, expected "
            f"“{expected_node}”.")
    if statement.get("currency_hash") != expected_currency_hash:
        findings.append(
            "Statement names a currency this platform did not assign it.")
    if int(statement.get("contract_serial", 0)) != expected_serial:
        findings.append(
            f"Contract serial {statement.get('contract_serial')} does not "
            f"match the assigned {expected_serial}.")

    drawn = int(statement.get("drawn_base_units", 0))
    allocated = int(statement.get("allocated_base_units", 0))
    if allocated > expected_allocated:
        findings.append(
            f"Branch claims {allocated} allocated; this platform sent "
            f"{expected_allocated}.")
    if drawn > allocated:
        findings.append(
            f"Branch drew {drawn} against an allocation of {allocated}.")

    return {"outcome": DISCREPANCY if findings else OK,
            "findings": findings, "statement": statement}
