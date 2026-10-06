"""Bind the request's user to the audit context for the life of the request."""

import uuid

from toto.audit.context import AuditContext, reset_context, set_context


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = set_context(AuditContext(
            user=getattr(request, "user", None),
            request=request,
            source="web",
            # One id per request, so the several records a single form submit
            # writes can be read back as one action.
            correlation_id=uuid.uuid4().hex,
        ))
        try:
            return self.get_response(request)
        finally:
            reset_context(token)


#: Vault url names -> the audit action they mean. Everything else in the
#: vault namespace that MUTATES (POST/PUT/PATCH/DELETE) is recorded under
#: the generic VAULT_ACTION with the url name in the metadata, so a new
#: door added by a suite pull is audited from day one rather than silently
#: unlisted until somebody maps it.
VAULT_ACTIONS = {
    "gateway_upload": "FILE_UPLOADED",
    "api_file_upload": "FILE_UPLOADED",
    "api_file_create": "FILE_CREATED",
    "create_file": "FILE_CREATED",
    "new_file": "FILE_CREATED",
    "api_file_content": "FILE_EDITED",
    "version_save": "FILE_EDITED",
    "version_restore": "FILE_RESTORED",
    "rename_file": "FILE_RENAMED",
    "move_file": "FILE_MOVED",
    "copy_files": "FILE_COPIED",
    "copy_files_ajax": "FILE_COPIED",
    "delete_file": "FILE_DELETED",
    "bulk_trash": "FILE_TRASHED",
    "bulk_move": "FILE_MOVED",
    "encrypt_file": "FILE_ENCRYPTED",
    "decrypt_file": "FILE_DECRYPTED",
    "create_zip": "FILE_ZIPPED",
    "api_directory_create": "DIRECTORY_CREATED",
    "api_directory_delete": "DIRECTORY_DELETED",
}

#: Reads that still belong in the trail: handing file BYTES out is a file
#: operation, a listing is not. GET-only.
VAULT_DOWNLOADS = {
    "public_file": "FILE_DOWNLOADED",
    "api_file_download": "FILE_DOWNLOADED",
    "download_encrypted": "FILE_DOWNLOADED",
    "peer_file_download": "FILE_DOWNLOADED",
}

#: Chatter that must never flood the chain: lock heartbeats fire every few
#: seconds from an open editor, and status endpoints are polled.
VAULT_IGNORED = {
    "lock_heartbeat", "lock_release",
    "encrypt_status", "zip_status", "transfer_status",
    "bucket_refresh_status",
}

#: Doors whose ordinary answers are chatter but whose security refusals are
#: not (2026-09-30). A claim on the editing lock is refused 403 when the caller
#: may read the file but not write it, and 404 when they may not see it — that
#: is somebody reaching for a document that is not theirs, and it is recorded
#: like a refused save. A granted claim and a 423 (a colleague is editing) stay
#: off the chain: an editor claims once on every page load.
VAULT_REFUSALS = {
    "lock_acquire": "FILE_LOCK_REFUSED",
}

_REFUSED = {403, 404}

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


class FileAuditMiddleware:
    """Every vault file operation lands in the hash-chained audit trail.

    The vault is vendored suite code; teaching it to call Placidia's audit
    would couple the suite to one host. This middleware watches the request
    instead: anything resolved into the ``vault`` namespace that mutates —
    or hands file bytes out — is appended to the chain, successes and
    refusals alike. §Dk1: the truth book records what happened to its files.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        match = getattr(request, "resolver_match", None)
        if match is None or match.namespace != "vault":
            return response
        name = match.url_name
        if name in VAULT_IGNORED:
            return response
        # A door that removed a file through ``toto.vault.trash`` has
        # recorded it there already — FILE_TRASHED or FILE_DELETED, which the
        # url alone cannot tell apart (2026-10-01). One act, one record.
        if getattr(request, "_vault_file_audited", False):
            return response

        action = None
        if name in VAULT_REFUSALS:
            if response.status_code not in _REFUSED:
                return response
            action = VAULT_REFUSALS[name]
        elif request.method in _MUTATING:
            action = VAULT_ACTIONS.get(name, "VAULT_ACTION")
        elif request.method == "GET" and name in VAULT_DOWNLOADS:
            action = VAULT_DOWNLOADS[name]
        if action is None:
            return response

        from toto.audit import record

        target = (match.kwargs.get("key") or match.kwargs.get("file_pk")
                  or match.kwargs.get("pk") or match.kwargs.get("bucket_slug")
                  # Several vault doors carry the file in the body instead
                  # of the url (delete, rename, move) — the trail should
                  # still name it.
                  or request.POST.get("file_pk", "")
                  or request.POST.get("key", "")
                  or "")
        try:
            record(
                action,
                app_label="vault",
                object_type="VaultFile" if "file" in name or "zip" in name
                            else "Vault",
                object_id=str(target),
                description=f"vault:{name}",
                actor_user=getattr(request, "user", None),
                request=request,
                source="vault",
                success=response.status_code < 400,
                metadata={"url_name": name, "method": request.method,
                          "status": response.status_code},
            )
        except Exception:  # noqa: BLE001 - the trail must never 500 a file op
            import logging

            logging.getLogger(__name__).exception(
                "Could not append the file operation to the audit trail.")
        return response
