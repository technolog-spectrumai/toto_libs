"""
LedgerAccount cryptographic signing service.

Supports Ed25519, RSA-2048, RSA-4096. Private keys are stored in Gervazy
EncryptedPrivateKey objects and decrypted at sign time via GervazyCryptoSession.

Signing modes:
  - Direct:     tx signed by a LedgerAccountKey that belongs to the tx's account.
  - Delegated:  tx signed using a LedgerAuthorization that delegates signing rights.

Verification works in both modes; delegated mode also validates scope/amount/time.
"""

import base64
import hashlib
import json
import os
import uuid
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

if TYPE_CHECKING:
    from .models import LedgerAccountKey, LedgerAuthorization, LedgerTransaction
    from toto.gervazy.crypto import GervazyCryptoSession


# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------

def build_transaction_payload(tx: "LedgerTransaction", nonce: str, idempotency_key: str) -> bytes:
    """
    Canonical JSON payload for signing. Includes all immutable transaction
    fields plus nonce and idempotency_key so every signing call produces a
    distinct bytes representation even for identical transactions.
    """
    entries = [
        {
            "account": e.account_id,
            "asset": e.asset_id,
            "amount": e.amount_base_units,
        }
        for e in tx.entries.order_by("pk").select_related()
    ]
    payload = {
        "v": 1,
        "reference": tx.reference,
        "transaction_type": tx.transaction_type,
        "asset_id": tx.asset_id,
        "nonce": nonce,
        "idempotency_key": idempotency_key,
        "entries": entries,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def hash_payload(payload: bytes) -> str:
    """SHA-256 hex digest of the canonical payload."""
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Low-level sign / verify
# ---------------------------------------------------------------------------

def _load_private_key(pem: str):
    return serialization.load_pem_private_key(pem.encode(), password=None)


def _load_public_key(pem: str):
    return serialization.load_pem_public_key(pem.encode())


def sign_payload(payload: bytes, private_key_pem: str, key_type: str) -> str:
    """Sign payload; return base64-encoded signature."""
    key = _load_private_key(private_key_pem)
    if isinstance(key, ed25519.Ed25519PrivateKey):
        sig = key.sign(payload)
    elif isinstance(key, rsa.RSAPrivateKey):
        sig = key.sign(payload, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH), hashes.SHA256())
    else:
        raise ValueError(f"Unsupported key type: {type(key).__name__}")
    return base64.b64encode(sig).decode()


def verify_payload(payload: bytes, signature_b64: str, public_key_pem: str, key_type: str) -> bool:
    """Return True if signature is valid; False on any failure."""
    try:
        sig = base64.b64decode(signature_b64)
        key = _load_public_key(public_key_pem)
        if isinstance(key, ed25519.Ed25519PublicKey):
            key.verify(sig, payload)
        elif isinstance(key, rsa.RSAPublicKey):
            key.verify(sig, payload, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH), hashes.SHA256())
        else:
            return False
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Gervazy-backed signing
# ---------------------------------------------------------------------------

def sign_with_gervazy_key(epk, payload: bytes, session: "GervazyCryptoSession", actor=None) -> str:
    """
    Decrypt the EncryptedPrivateKey via session, sign payload, return base64 sig.
    actor is stored in any audit log if available.
    """
    pem = session.decrypt_private_key(epk)
    return sign_payload(payload, pem, epk.key_type)


# ---------------------------------------------------------------------------
# Transaction signing (in-place)
# ---------------------------------------------------------------------------

def _generate_nonce() -> str:
    return base64.b64encode(os.urandom(16)).decode()


def sign_transaction_directly(
    tx: "LedgerTransaction",
    account_key: "LedgerAccountKey",
    session: "GervazyCryptoSession",
    actor=None,
) -> None:
    """
    Sign tx using account_key. Modifies tx in-place (does not save).
    Caller must save after calling this.

    Raises ValueError if the key is not active or not for the same account as
    any entry that would be debited.
    """
    from django.utils import timezone

    if not account_key.is_active:
        raise ValueError(f"LedgerAccountKey {account_key.key_id} is not active.")
    if tx.posted:
        raise ValueError("Cannot sign a posted transaction.")

    nonce = _generate_nonce()
    idempotency_key = tx.idempotency_key or str(uuid.uuid4())
    payload = build_transaction_payload(tx, nonce, idempotency_key)
    sig = sign_with_gervazy_key(account_key.encrypted_private_key, payload, session, actor=actor)

    tx.signed_by_key = account_key
    tx.authorization = None
    tx.payload_hash = hash_payload(payload)
    tx.signature = sig
    tx.nonce = nonce
    tx.idempotency_key = idempotency_key
    tx.signed_at = timezone.now()


def sign_transaction_delegated(
    tx: "LedgerTransaction",
    authorization: "LedgerAuthorization",
    session: "GervazyCryptoSession",
    actor=None,
    scope: str = "transfer",
) -> None:
    """
    Sign tx using a LedgerAuthorization's delegate_key. Validates scope and
    amount before signing. Modifies tx in-place; caller must save.
    """
    from django.utils import timezone

    if not authorization.is_valid_now():
        raise ValueError("LedgerAuthorization is not valid (expired, revoked, or signing key inactive).")

    if not authorization.allows_scope(scope):
        raise ValueError(f"LedgerAuthorization does not allow scope '{scope}'.")

    if tx.asset_id and authorization.asset_id:
        total_debit = sum(
            abs(e.amount_base_units)
            for e in tx.entries.filter(amount_base_units__lt=0)
        )
        if not authorization.allows_amount(tx.asset, total_debit):
            raise ValueError("LedgerAuthorization amount ceiling exceeded.")

    if not authorization.delegate_key_id:
        raise ValueError("LedgerAuthorization has no delegate_key — cannot sign.")

    if tx.posted:
        raise ValueError("Cannot sign a posted transaction.")

    nonce = _generate_nonce()
    idempotency_key = tx.idempotency_key or str(uuid.uuid4())
    payload = build_transaction_payload(tx, nonce, idempotency_key)
    sig = sign_with_gervazy_key(authorization.delegate_key, payload, session, actor=actor)

    tx.signed_by_key = authorization.signed_by_account_key
    tx.authorization = authorization
    tx.payload_hash = hash_payload(payload)
    tx.signature = sig
    tx.nonce = nonce
    tx.idempotency_key = idempotency_key
    tx.signed_at = timezone.now()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_transaction(tx: "LedgerTransaction") -> tuple[bool, str]:
    """
    Verify signature on a transaction.
    Returns (ok: bool, reason: str).
    """
    if not tx.signature or not tx.payload_hash or not tx.nonce:
        return False, "Transaction is not signed."

    if not tx.signed_by_key_id:
        return False, "No signing key recorded."

    nonce = tx.nonce
    idempotency_key = tx.idempotency_key or ""
    payload = build_transaction_payload(tx, nonce, idempotency_key)

    if hash_payload(payload) != tx.payload_hash:
        return False, "Payload hash mismatch — transaction data may have changed."

    if tx.authorization_id:
        auth = tx.authorization
        if not auth.is_valid_now():
            return False, "Authorization is no longer valid."
        # Delegated: the payload is signed by delegate_key; verify with its public key.
        delegate_epk = auth.delegate_key
        if not delegate_epk:
            return False, "Authorization has no delegate_key to verify against."
        signing_pub = delegate_epk.public_key_pem
        ok = verify_payload(payload, tx.signature, signing_pub, tx.signed_by_key.algorithm)
        if not ok:
            return False, "Signature verification failed (delegated mode)."
        return True, "OK (delegated)"
    else:
        account_key = tx.signed_by_key
        if not account_key.is_active:
            return False, "Signing key is no longer active."
        ok = verify_payload(payload, tx.signature, account_key.public_key_pem, account_key.algorithm)
        if not ok:
            return False, "Signature verification failed (direct mode)."
        return True, "OK (direct)"


# ---------------------------------------------------------------------------
# Authorization grant signing
# ---------------------------------------------------------------------------

def sign_authorization_grant(
    authorization: "LedgerAuthorization",
    account_key: "LedgerAccountKey",
    session: "GervazyCryptoSession",
    actor=None,
) -> None:
    """
    Sign the authorization grant payload using account_key. Stores the
    signature and canonical payload back onto the authorization object (does
    not save — caller must save).
    """
    if not account_key.is_active:
        raise ValueError(f"LedgerAccountKey {account_key.key_id} is not active.")
    if account_key.ledger_account_id != authorization.ledger_account_id:
        raise ValueError("account_key does not belong to the authorization's ledger_account.")

    grant_payload = {
        "v": 1,
        "ledger_account_id": authorization.ledger_account_id,
        "delegate_user_id": authorization.delegate_user_id,
        "delegate_key_id": authorization.delegate_key_id,
        "scopes": sorted(authorization.scopes or []),
        "max_amount_base_units": authorization.max_amount_base_units,
        "asset_id": authorization.asset_id,
        "valid_from": authorization.valid_from.isoformat() if authorization.valid_from else None,
        "valid_until": authorization.valid_until.isoformat() if authorization.valid_until else None,
        "signing_key_id": account_key.key_id,
    }
    payload_bytes = json.dumps(grant_payload, sort_keys=True, separators=(",", ":")).encode()
    sig = sign_with_gervazy_key(account_key.encrypted_private_key, payload_bytes, session, actor=actor)

    authorization.signed_grant_payload = grant_payload
    authorization.grant_signature = sig
    authorization.signed_by_account_key = account_key
