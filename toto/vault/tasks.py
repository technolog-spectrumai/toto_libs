"""Celery tasks for the vault app.

Heavy file operations (encrypt) download from object storage, run crypto, then
re-upload — easily long enough to outlive an HTTP request, and a long synchronous
request with no bytes flowing is exactly what a Tor circuit drops. When
``VAULT_ENCRYPT_ASYNC`` is on, ``EncryptFileView`` dispatches encryption here and
the browser polls ``EncryptStatusView`` for the result.

Security note: the password is a task argument, so it transits the (internal-only)
Redis broker briefly until the worker acks the message. It is never written to the
result backend — the task returns only ``{ok, raw_url}`` / ``{ok, error}``. The
broker lives on the same trusted host as the at-rest key material, so this stays
within the existing threat model. Ownership is enforced by the view *before*
dispatch, so the task trusts ``file_pk``.
"""
from __future__ import annotations

from celery import shared_task


@shared_task
def encrypt_vault_file(file_pk, password, owner_password=None) -> dict:
    from .models import VaultFile  # lazy import — keep app loading off task import

    try:
        vault_file = VaultFile.objects.get(pk=file_pk)
    except VaultFile.DoesNotExist:
        return {"ok": False, "error": "File not found."}
    if vault_file.is_encrypted:
        return {"ok": False, "error": "File is already encrypted."}
    try:
        vault_file.encrypt(password=password, owner_password=owner_password)
        vault_file.is_public = False
        vault_file.save(update_fields=["is_public"])
    except Exception as e:  # noqa: BLE001 — surface the failure reason to the client
        msg = str(e)
        if "EOF marker not found" in msg or "PdfRead" in type(e).__name__:
            msg = "File does not appear to be a valid PDF."
        return {"ok": False, "error": msg}
    return {"ok": True, "raw_url": vault_file.get_public_url() or ""}
