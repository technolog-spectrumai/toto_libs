"""
Workflow predefined tasks for vault jobs: archiving, encryption's node marker,
and the remote-bucket mirror refresh.

Only imported by toto.workflows' autodiscover (workflows/apps.py) for installed
apps, so the ``toto.workflows`` import below is safe — when workflows is absent
this module is never loaded.
"""
from toto.workflows.predefined_tasks import register


@register("vault_zip_files")
def vault_zip_files(input_data: dict) -> dict:
    """Zip the selected vault files into a new ``zip`` VaultFile.

    Expects input_data = {"data": {owner_id, source_directory_id,
    target_directory_id (nullable), file_ids, output_name}}.
    """
    from django.contrib.auth.models import User

    from toto.vault.archive import zip_files_to_vault_file
    from toto.vault.models import VaultDirectory

    data = input_data.get("data") or {}
    owner = User.objects.get(pk=data["owner_id"])
    source = VaultDirectory.objects.get(pk=data["source_directory_id"])
    target = (
        VaultDirectory.objects.get(pk=data["target_directory_id"])
        if data.get("target_directory_id")
        else None
    )
    vault_file, n_added = zip_files_to_vault_file(
        owner, source, target, data.get("file_ids") or [], data.get("output_name") or "",
    )
    return {"data": {"vault_file_id": vault_file.pk, "added": n_added}}


@register("vault_encrypt_file")
def vault_encrypt_file(input_data: dict) -> dict:
    """Node marker for the ``vault-encrypt`` workflow.

    Encryption needs the user's password, which is deliberately NEVER persisted
    to ``WorkflowRun.input_data`` (see ``vault/tasks.py``). So the run is driven by
    the dedicated ``encrypt_workflow_run`` Celery task, which carries the password
    as a transient broker arg — the generic executor never reaches this node with a
    password. If it is ever invoked through the vanilla engine, fail loudly rather
    than silently no-op.
    """
    raise RuntimeError(
        "vault_encrypt_file must be run via the vault encrypt action "
        "(encrypt_workflow_run), which supplies the password out-of-band."
    )


@register("vault_refresh_remote_bucket")
def vault_refresh_remote_bucket(input_data: dict) -> dict:
    """Walk a paired host's bucket listing and bring the metadata stubs up to
    date. Expects input_data = {"data": {"run_id": <BucketRefreshRun pk>}}."""
    from .mirror import RefreshStatus, execute_refresh_run

    run_id = (input_data.get("data") or {}).get("run_id")
    if run_id is None:
        raise ValueError(
            "vault_refresh_remote_bucket requires run_id in its input data.")

    run = execute_refresh_run(run_id)
    if run.status == RefreshStatus.FAILED:
        # Raise so the WorkflowRun shows FAILED too; the sentence is already
        # on the row.
        raise RuntimeError(run.error or "The refresh failed.")
    return {"data": {"run_id": run_id, "status": run.status}}


@register("vault_transfer_files")
def vault_transfer_files(input_data: dict) -> dict:
    """Copy a frozen selection between buckets, resuming at the cursor.
    Expects input_data = {"data": {"run_id": <TransferRun pk>}}."""
    from . import transfer_dispatch
    from .transfer import TransferStatus
    from .transfer_runner import SoftTimeLimitExceeded, execute_transfer_run

    run_id = (input_data.get("data") or {}).get("run_id")
    if run_id is None:
        raise ValueError(
            "vault_transfer_files requires run_id in its input data.")

    try:
        run = execute_transfer_run(run_id)
    except SoftTimeLimitExceeded:
        # The runner leaves the cursor for a resume and the closing to its
        # caller. Otherwise only the stuck-run sweeper closes this row, an
        # hour on, and until then Retry answers "still going" and refuses
        # any other run between the same two buckets.
        transfer_dispatch.fail_transfer_run(
            run_id, "The worker's time ran out; Retry continues from where "
                    "it stopped.")
        raise
    if run.status == TransferStatus.FAILED:
        raise RuntimeError(run.error or "The transfer failed.")
    return {"data": {"run_id": run_id, "status": run.status,
                     "files_done": run.files_done,
                     "files_skipped": run.files_skipped}}
