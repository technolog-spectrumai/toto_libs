"""Celery tasks for the vault app.

Heavy file operations (encrypt) download from object storage, run crypto, then
re-upload — easily long enough to outlive an HTTP request, and a long synchronous
request with no bytes flowing is exactly what a Tor circuit drops. ``EncryptFileView``
dispatches encryption here as its own ``vault-encrypt`` workflow run and the browser
polls ``EncryptStatusView`` for the result.

Security note: the password is a task argument, so it transits the (internal-only)
Redis broker briefly until the worker acks the message. It is **never** written to the
DB — not to ``WorkflowRun.input_data``/``WorkflowNodeRun.input_data`` (which carry only
``file_pk``/``owner_id``) nor to the result backend (the task returns only
``{ok, raw_url}`` / ``{ok, error}``). The broker lives on the same trusted host as the
at-rest key material, so this stays within the existing threat model. Ownership is
enforced by the view *before* dispatch, so the task trusts ``file_pk``.
"""
from __future__ import annotations

from celery import shared_task


def _pdf_friendly_error(exc: Exception) -> str:
    msg = str(exc)
    if "EOF marker not found" in msg or "PdfRead" in type(exc).__name__:
        return "File does not appear to be a valid PDF."
    return msg


@shared_task
def encrypt_workflow_run(run_id, password, owner_password=None) -> dict:
    """Drive a ``vault-encrypt`` WorkflowRun to completion, encrypting its file.

    Reads ``file_pk`` from the (already-persisted, password-free) run input, then
    writes real ``WorkflowNodeRun``/``WorkflowRun`` records for status + UI while
    keeping the password as a transient argument only.
    """
    from django.utils import timezone

    from toto.workflows.models import WorkflowNode, WorkflowNodeRun, WorkflowRun

    from .models import VaultFile  # lazy import — keep app loading off task import

    try:
        run = WorkflowRun.objects.select_related("workflow").get(pk=run_id)
    except WorkflowRun.DoesNotExist:
        return {"ok": False, "error": "Workflow run not found."}

    data = (run.input_data or {}).get("data") or {}
    file_pk = data.get("file_pk")

    # Mark the run + its single node running.
    run.status = WorkflowRun.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at"])

    node = run.workflow.nodes.filter(
        node_type=WorkflowNode.PREDEFINED_TASK, task_name="vault_encrypt_file"
    ).first()
    node_run = None
    if node is not None:
        node_run, _ = WorkflowNodeRun.objects.get_or_create(
            workflow_run=run,
            node=node,
            defaults={
                "status": WorkflowNodeRun.RUNNING,
                "input_data": {"data": {"file_pk": file_pk, "owner_id": data.get("owner_id")}},
                "started_at": timezone.now(),
            },
        )

    def _fail(message: str) -> dict:
        now = timezone.now()
        if node_run is not None:
            node_run.status = WorkflowNodeRun.FAILED
            node_run.error = message
            node_run.completed_at = now
            node_run.save(update_fields=["status", "error", "completed_at"])
        run.status = WorkflowRun.FAILED
        run.output_data = {"ok": False, "error": message}
        run.completed_at = now
        run.save(update_fields=["status", "output_data", "completed_at"])
        return {"ok": False, "error": message}

    try:
        vault_file = VaultFile.objects.get(pk=file_pk)
    except VaultFile.DoesNotExist:
        return _fail("File not found.")
    if vault_file.is_encrypted:
        return _fail("File is already encrypted.")

    try:
        vault_file.encrypt(password=password, owner_password=owner_password)
        vault_file.is_public = False
        vault_file.save(update_fields=["is_public"])
    except Exception as exc:  # noqa: BLE001 — surface the failure reason to the client
        return _fail(_pdf_friendly_error(exc))

    output = {"data": {"vault_file_id": vault_file.pk, "raw_url": vault_file.get_public_url() or ""}}
    now = timezone.now()
    if node_run is not None:
        node_run.status = WorkflowNodeRun.COMPLETED
        node_run.output_data = output
        node_run.completed_at = now
        node_run.save(update_fields=["status", "output_data", "completed_at"])
    run.status = WorkflowRun.COMPLETED
    run.output_data = {"ok": True, **output["data"]}
    run.completed_at = now
    run.save(update_fields=["status", "output_data", "completed_at"])
    return {"ok": True, "raw_url": output["data"]["raw_url"]}


@shared_task(name="toto.vault.tasks.purge_bucket_task", soft_time_limit=1500)
def purge_bucket_task(bucket_pk, actor_pk=None) -> dict:
    """Storage → Management's Delete, on a worker: every file of the bucket
    (row and bytes, S3 objects; a mount's listing rows only), then the bucket.

    Carries ids only — never a credential: the S3 key is opened from the
    bucket's sealed secret inside the job and dies with it. Safe to run twice
    (``bucket_lifecycle.purge_bucket`` resumes and is a no-op once the bucket
    is gone).

    One run works for at most ``PURGE_BUDGET_SECONDS`` and then queues the
    next, so none runs near the time limits. Nothing ends a run silently: the
    soft time limit, an error that escapes the purge, and the worker being
    stopped are stamped on ``deletion_error`` and audited
    (``bucket_lifecycle.stop_purge``) — the bucket then reads "Deletion
    stopped" and offers Delete again, which resumes."""
    import logging

    from celery.exceptions import SoftTimeLimitExceeded
    from django.utils.translation import gettext

    from .bucket_lifecycle import PURGE_BUDGET_SECONDS, purge_bucket, queue_purge, stop_purge

    try:
        result = purge_bucket(bucket_pk, actor_pk=actor_pk, budget=PURGE_BUDGET_SECONDS)
    except SoftTimeLimitExceeded:
        # Every file handled so far is gone for good; the rest waits for the
        # next confirmation, which resumes where this stopped.
        return stop_purge(bucket_pk, gettext(
            "The deletion ran out of time before every file was gone. Confirm "
            "Delete again to continue."), actor_pk=actor_pk)
    except Exception as exc:  # noqa: BLE001 - whatever escaped: said, not hidden
        logging.getLogger("toto.vault").exception("vault: the purge of bucket %s failed", bucket_pk)
        return stop_purge(bucket_pk, gettext(
            "The deletion stopped on an error: %(reason)s. Confirm Delete again to "
            "continue.") % {"reason": str(exc)[:300]}, actor_pk=actor_pk)
    except BaseException:
        # The worker is going away (a warm shutdown's SystemExit, an
        # interrupt): say so while there is still a process to say it.
        stop_purge(bucket_pk, gettext(
            "The deletion was interrupted because the worker stopped. Confirm "
            "Delete again to continue."), actor_pk=actor_pk)
        raise
    if result.get("more") and not queue_purge(bucket_pk, actor_pk=actor_pk):
        return stop_purge(bucket_pk, gettext(
            "The deletion paused and no worker took the rest. Start one, then "
            "confirm Delete again to continue."), actor_pk=actor_pk)
    return result
