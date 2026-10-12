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


#: What a member reads when the file is no PDF the library can read.
NOT_A_PDF = "File does not appear to be a valid PDF."


def _pdf_friendly_error(exc: Exception) -> str:
    """The reason a member reads when a PDF could not be encrypted.

    A file pypdf cannot read as a PDF raises its ``PdfReadError`` or a
    subclass — ``PdfStreamError`` ("Stream has ended unexpectedly") for one
    cut short or no PDF at all, ``EmptyFileError`` for an empty one — and each
    reads as ``NOT_A_PDF``. This went by the class's NAME while PyPDF2 raised
    ``PdfReadError("EOF marker not found")`` itself; pypdf (37c.30) raises the
    subclasses, so their own words reached the member (2026-10-02, 41.4). A
    PDF already locked with a password (``FileNotDecryptedError``) keeps its
    own sentence, as it did."""
    msg = str(exc)
    try:
        from pypdf.errors import FileNotDecryptedError, PdfReadError
    except ImportError:  # no pypdf here: nothing of its to recognise
        FileNotDecryptedError = PdfReadError = ()
    if isinstance(exc, PdfReadError) and not isinstance(exc, FileNotDecryptedError):
        return NOT_A_PDF
    if "EOF marker not found" in msg or "PdfRead" in type(exc).__name__:
        return NOT_A_PDF
    return msg


def _drive_cipher_run(run_id, op, password, owner_password=None) -> dict:
    """Drive a ``vault-encrypt`` or ``vault-decrypt`` WorkflowRun to completion.

    Reads ``file_pk`` from the (already-persisted, password-free) run input, then
    writes real ``WorkflowNodeRun``/``WorkflowRun`` records for status + UI while
    keeping the password as a transient argument only.
    """
    from django.utils import timezone

    from toto.workflows.models import WorkflowNode, WorkflowNodeRun, WorkflowRun

    from . import cipher_jobs
    from .models import VaultFile  # lazy import — keep app loading off task import
    from .workflow import DECRYPT_TASK_NAME, ENCRYPT_TASK_NAME

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
        node_type=WorkflowNode.PREDEFINED_TASK,
        task_name=ENCRYPT_TASK_NAME if op == cipher_jobs.ENCRYPT else DECRYPT_TASK_NAME,
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
    if op == cipher_jobs.ENCRYPT and vault_file.is_encrypted:
        return _fail("File is already encrypted.")
    if op == cipher_jobs.DECRYPT and not vault_file.is_encrypted:
        return _fail("File is not encrypted.")

    try:
        done = cipher_jobs.perform(vault_file, op, password, owner_password,
                                   publish=bool(data.get("publish")))
    except Exception as exc:  # noqa: BLE001 — surface the failure reason to the client
        return _fail(_pdf_friendly_error(exc) if op == cipher_jobs.ENCRYPT else str(exc))

    output = {"data": done}
    now = timezone.now()
    if node_run is not None:
        node_run.status = WorkflowNodeRun.COMPLETED
        node_run.output_data = output
        node_run.completed_at = now
        node_run.save(update_fields=["status", "output_data", "completed_at"])
    run.status = WorkflowRun.COMPLETED
    run.output_data = {"ok": True, **done}
    run.completed_at = now
    run.save(update_fields=["status", "output_data", "completed_at"])
    if op == cipher_jobs.ENCRYPT:
        return {"ok": True, "raw_url": done["raw_url"]}
    return {"ok": True, "url": done["url"]}


@shared_task
def encrypt_workflow_run(run_id, password, owner_password=None) -> dict:
    """Drive a ``vault-encrypt`` WorkflowRun to completion, encrypting its file."""
    return _drive_cipher_run(run_id, "encrypt", password, owner_password)


@shared_task
def decrypt_workflow_run(run_id, password) -> dict:
    """Drive a ``vault-decrypt`` WorkflowRun to completion (2026-10-10): the
    encrypt run's twin, for a host that keeps decryption off the request
    (``cipher_jobs``)."""
    return _drive_cipher_run(run_id, "decrypt", password)


@shared_task
def cipher_file_task(job_id, file_pk, op, password, owner_password=None, publish=False) -> dict:
    """Encrypt or decrypt one file on the worker, without a workflow run
    (2026-10-10): a file up to the host's limit (``cipher_jobs``). The password
    is an argument and nothing else; the outcome goes to the job's cache entry,
    which the page polls. Never raises: a failure is the job's answer."""
    from . import cipher_jobs
    from .models import VaultFile

    def _end(result: dict) -> dict:
        cipher_jobs.finish(job_id, result)
        return {key: value for key, value in result.items() if key != "vault_file_id"}

    vault_file = VaultFile.objects.filter(pk=file_pk).first()
    if vault_file is None:
        return _end({"ok": False, "error": "File not found."})
    if op == cipher_jobs.ENCRYPT and vault_file.is_encrypted:
        return _end({"ok": False, "error": "File is already encrypted."})
    if op == cipher_jobs.DECRYPT and not vault_file.is_encrypted:
        return _end({"ok": False, "error": "File is not encrypted."})
    try:
        done = cipher_jobs.perform(vault_file, op, password, owner_password, publish=publish)
    except Exception as exc:  # noqa: BLE001 — the reason is the member's to read
        return _end({"ok": False,
                     "error": _pdf_friendly_error(exc) if op == cipher_jobs.ENCRYPT else str(exc)})
    return _end({"ok": True, **done})


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


@shared_task(name="toto.vault.tasks.purge_expired_trash", soft_time_limit=1500)
def purge_expired_trash() -> dict:
    """The nightly trash purge (2026-10-01): files trashed more than
    ``VAULT_TRASH_DAYS`` ago are deleted for good (``trash.purge_expired``).
    Idempotent — a second fire finds nothing due; a file that failed stays in
    the trash, its reason logged, for the next night.

    Where the workflow engine is installed the night's purge is a run of the
    ``vault-trash-purge`` workflow (2026-10-11), driven here, inside this
    task: the Workflows page then shows each night's cleanup with its counts.
    If the engine cannot run it, the purge is done directly: the cleanup is
    never skipped for the sake of its record."""
    from django.apps import apps

    from .trash import purge_expired

    if apps.is_installed("toto.workflows"):
        import logging

        try:
            return _purge_trash_as_workflow()
        except Exception:  # noqa: BLE001 - the cleanup matters more than its run
            logging.getLogger("toto.vault").exception(
                "trash cleanup: the workflow run failed; purging directly")
    return purge_expired()


def _purge_trash_as_workflow() -> dict:
    from toto.workflows.models import WorkflowRun
    from toto.workflows.services.executor import WorkflowExecutor

    from .workflow import ensure_trash_workflow

    run = WorkflowRun.objects.create(workflow=ensure_trash_workflow(), input_data={"data": {}})
    WorkflowExecutor(async_lambdas=False).start(run)
    run.refresh_from_db()
    if run.status != WorkflowRun.COMPLETED:
        raise RuntimeError(f"trash cleanup run {run.pk} ended {run.status}")
    # The counts are the node's output; they are put on the run too, where
    # the run's page shows them.
    node_run = run.node_runs.order_by("pk").first()
    counts = dict(((node_run.output_data or {}).get("data") or {}) if node_run else {})
    if not run.output_data:
        run.output_data = counts
        run.save(update_fields=["output_data"])
    return {**counts, "workflow_run_id": run.pk}
