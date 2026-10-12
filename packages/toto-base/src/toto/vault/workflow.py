"""The one-node workflows vault jobs are dispatched through.

Get-or-create with node self-heal, mirroring antivirus, texlab and steven: a
missing workflow row must never be a stuck state an operator has to diagnose.
"""

REFRESH_WORKFLOW_SLUG = "vault-remote-refresh"
REFRESH_TASK_NAME = "vault_refresh_remote_bucket"

TRANSFER_WORKFLOW_SLUG = "vault-transfer"
TRANSFER_TASK_NAME = "vault_transfer_files"

ENCRYPT_WORKFLOW_SLUG = "vault-encrypt"
ENCRYPT_TASK_NAME = "vault_encrypt_file"

DECRYPT_WORKFLOW_SLUG = "vault-decrypt"
DECRYPT_TASK_NAME = "vault_decrypt_file"

TRASH_WORKFLOW_SLUG = "vault-trash-purge"
TRASH_TASK_NAME = "vault_purge_trash"


def _ensure_one_node(slug, task_name, name, description, label):
    from toto.workflows.models import Workflow, WorkflowNode

    workflow, _ = Workflow.objects.get_or_create(
        slug=slug, defaults={"name": name, "description": description})
    if not workflow.nodes.filter(task_name=task_name).exists():
        WorkflowNode.objects.create(
            workflow=workflow,
            node_type=WorkflowNode.PREDEFINED_TASK,
            label=label,
            task_name=task_name,
            position_x=0,
            position_y=0,
        )
    return workflow


def ensure_encrypt_workflow():
    """Get-or-create the encrypt workflow. Idempotent. Its run is driven by
    ``tasks.encrypt_workflow_run``, which carries the password out of band."""
    return _ensure_one_node(
        ENCRYPT_WORKFLOW_SLUG, ENCRYPT_TASK_NAME, "Encrypt file",
        "Encrypt a vault file at rest. The password is supplied "
        "out-of-band and never stored on the run.",
        "Encrypt file")


def ensure_decrypt_workflow():
    """Get-or-create the decrypt workflow (2026-10-10). Idempotent. Its run is
    driven by ``tasks.decrypt_workflow_run``, the encrypt one's twin."""
    return _ensure_one_node(
        DECRYPT_WORKFLOW_SLUG, DECRYPT_TASK_NAME, "Decrypt file",
        "Decrypt a vault file with its password. The password is supplied "
        "out-of-band and never stored on the run.",
        "Decrypt file")


def ensure_refresh_workflow():
    """Get-or-create the mirror-refresh workflow. Idempotent."""
    from toto.workflows.models import Workflow, WorkflowNode

    workflow, _ = Workflow.objects.get_or_create(
        slug=REFRESH_WORKFLOW_SLUG,
        defaults={
            "name": "Remote bucket refresh",
            "description": ("Walk a paired host's bucket listing and bring "
                            "the local metadata stubs up to date."),
        },
    )
    if not workflow.nodes.filter(
            node_type=WorkflowNode.PREDEFINED_TASK,
            task_name=REFRESH_TASK_NAME).exists():
        WorkflowNode.objects.create(
            workflow=workflow,
            node_type=WorkflowNode.PREDEFINED_TASK,
            task_name=REFRESH_TASK_NAME,
            label="refresh the mirror",
        )
    return workflow


def ensure_transfer_workflow():
    """Get-or-create the transfer workflow. Idempotent."""
    from toto.workflows.models import Workflow, WorkflowNode

    workflow, _ = Workflow.objects.get_or_create(
        slug=TRANSFER_WORKFLOW_SLUG,
        defaults={
            "name": "Bucket transfer",
            "description": ("Copy the selected files between buckets when at "
                            "least one end is not this host's disk."),
        },
    )
    if not workflow.nodes.filter(
            node_type=WorkflowNode.PREDEFINED_TASK,
            task_name=TRANSFER_TASK_NAME).exists():
        WorkflowNode.objects.create(
            workflow=workflow,
            node_type=WorkflowNode.PREDEFINED_TASK,
            task_name=TRANSFER_TASK_NAME,
            label="copy the files",
        )
    return workflow


def ensure_trash_workflow():
    """Get-or-create the trash cleanup workflow (2026-10-11). Idempotent. The
    nightly Celery task ``tasks.purge_expired_trash`` runs it, so each night's
    cleanup is a run an operator can open."""
    return _ensure_one_node(
        TRASH_WORKFLOW_SLUG, TRASH_TASK_NAME, "Trash cleanup",
        "Delete for good the files that have waited in the trash longer than "
        "this platform keeps them.",
        "Purge the expired trash")
