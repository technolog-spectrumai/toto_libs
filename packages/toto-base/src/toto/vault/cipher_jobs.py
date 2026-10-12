"""Encrypting and decrypting a file off the request (2026-10-10).

A host that sets ``VAULT_ENCRYPT_WORKFLOW_MB`` (megabytes, 0 or more) never
runs either in the request. Both go to the Celery worker, always; a file
LARGER than the limit goes as a workflow run (``vault-encrypt`` /
``vault-decrypt``: an operator sees it on the Workflows page with its times
and its outcome), a file up to the limit as a plain task. With no worker
listening the door refuses and says so: it does not fall back to the request.

A host that leaves the setting unset keeps what it had: ``VAULT_ENCRYPT_ASYNC``
sends every encryption through the workflow, otherwise the request does it.

The password travels as a task argument only (``tasks.py`` says what that
means); a job's state is kept in the cache, never in the database: who asked,
which file, and at the end ``{ok, raw_url}`` / ``{ok, url}`` / ``{ok, error}``.
The cache must be one the web process and the worker share (Redis). Where it
is not, the status door still learns a success from the file itself.
"""
from __future__ import annotations

import uuid

from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext as _

ENCRYPT = "encrypt"
DECRYPT = "decrypt"

#: How long a plain job's state is kept: far longer than the page polls.
JOB_TTL = 60 * 60
_KEY = "vault:cipher-job:{}"


from toto.quota.charge import InsufficientFunds  # noqa: F401 - raised by dispatch


class NoWorker(Exception):
    """No Celery worker answered: nothing was queued."""


def no_worker_sentence() -> str:
    return _("The platform's background worker is not running, so this cannot be done "
             "now. Try again later, or tell an administrator.")


def limit_mb() -> int | None:
    """The host's limit in megabytes; ``None`` when the host did not set one."""
    raw = getattr(settings, "VAULT_ENCRYPT_WORKFLOW_MB", None)
    if raw is None or raw == "":
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return None


def off_request() -> bool:
    """Whether this host sends every encryption and decryption to the worker."""
    return limit_mb() is not None


def wants_workflow(vault_file) -> bool:
    """A file larger than the limit runs as a workflow run, when the engine
    is installed."""
    limit = limit_mb()
    if limit is None or not apps.is_installed("toto.workflows"):
        return False
    return (vault_file.file_size_bytes or 0) > limit * 1024 * 1024


def worker_ready() -> bool:
    """A worker is listening, or tasks run in the caller (a test, a checkout)."""
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return True
    from toto.celery_utils import celery_available

    return celery_available()


def perform(vault_file, op, password, owner_password=None, *, publish=False) -> dict:
    """The act itself, on the worker; what the door answers with. Raises what
    the strategy raises."""
    if op == ENCRYPT:
        vault_file.encrypt(password=password, owner_password=owner_password)
        vault_file.is_public = False
        vault_file.save(update_fields=["is_public"])
        return {"vault_file_id": vault_file.pk, "raw_url": vault_file.get_public_url() or ""}
    vault_file.decrypt(password=password)
    if publish:
        vault_file.is_public = True
        vault_file.save(update_fields=["is_public"])
    return {"vault_file_id": vault_file.pk, "url": vault_file.get_public_url() or ""}


def dispatch(vault_file, user, op, password, owner_password=None, *, publish=False) -> dict:
    """Queue ``op`` on ``vault_file`` for the worker and say how to follow it:
    ``{"ok", "async", "workflow_run_id", "workflow_run_url"}`` for a workflow
    run, ``{"ok", "async", "job_id"}`` for a plain task. The caller has checked
    that ``user`` may do this to the file. Raises :class:`NoWorker`, and
    ``toto.quota.charge.InsufficientFunds`` where the work's cost (the file's
    megabytes, ``compute.py``) is not covered."""
    if not worker_ready():
        raise NoWorker(no_worker_sentence())
    # What the work costs, by the file's size (compute.py): checked before
    # anything is queued, taken once it is. Raises InsufficientFunds.
    from . import compute

    mb = compute.megabytes(vault_file.file_size_bytes)
    compute.check(user, mb)
    if wants_workflow(vault_file):
        answer = _dispatch_workflow(vault_file, user, op, password, owner_password, publish)
        compute.charge(user, mb, source_type="workflows.WorkflowRun", source_id=answer["workflow_run_id"])
        return answer
    from .tasks import cipher_file_task

    job_id = uuid.uuid4().hex
    cache.set(_KEY.format(job_id), {"owner_id": user.pk, "file_pk": vault_file.pk, "op": op,
                                    "done": False}, JOB_TTL)
    cipher_file_task.delay(job_id, vault_file.pk, op, password, owner_password, publish)
    compute.charge(user, mb, source_type="vault.VaultFile", source_id=vault_file.pk)
    return {"ok": True, "async": True, "job_id": job_id}


def _dispatch_workflow(vault_file, user, op, password, owner_password, publish) -> dict:
    from django.urls import NoReverseMatch, reverse

    from toto.workflows.models import WorkflowRun

    from . import workflow as vault_workflow
    from .tasks import decrypt_workflow_run, encrypt_workflow_run

    if op == ENCRYPT:
        wf = vault_workflow.ensure_encrypt_workflow()
    else:
        wf = vault_workflow.ensure_decrypt_workflow()
    data = {"file_pk": vault_file.pk, "owner_id": user.pk}
    if op == DECRYPT and publish:
        data["publish"] = True
    run = WorkflowRun.objects.create(workflow=wf, input_data={"data": data}, started_by=user)
    if op == ENCRYPT:
        encrypt_workflow_run.delay(run.pk, password, owner_password)
    else:
        decrypt_workflow_run.delay(run.pk, password)
    try:
        run_url = reverse("workflows:workflow_run_detail", args=[run.pk])
    except NoReverseMatch:
        run_url = ""
    return {"ok": True, "async": True, "workflow_run_id": run.pk, "workflow_run_url": run_url}


def finish(job_id, result: dict) -> None:
    """The worker's word on a plain job: ``result`` is what ``status`` hands on."""
    key = _KEY.format(job_id)
    state = cache.get(key) or {}
    state.update({"done": True, "result": result})
    cache.set(key, state, JOB_TTL)


def status(job_id, user) -> dict | None:
    """What the page polls for a plain job; ``None`` when there is no such job
    of this user's. ``{"done": False}`` while it runs."""
    state = cache.get(_KEY.format(job_id))
    if not state or (state.get("owner_id") != user.pk and not user.is_superuser):
        return None
    if state.get("done"):
        return {"done": True, **(state.get("result") or {})}
    # A cache the worker does not share never hears the end: ask the file.
    from .models import VaultFile

    vault_file = VaultFile.objects.filter(pk=state.get("file_pk")).first()
    if vault_file is not None:
        if state.get("op") == ENCRYPT and vault_file.is_encrypted:
            return {"done": True, "ok": True, "vault_file_id": vault_file.pk,
                    "raw_url": vault_file.get_public_url() or ""}
        if state.get("op") == DECRYPT and not vault_file.is_encrypted:
            return {"done": True, "ok": True, "vault_file_id": vault_file.pk,
                    "url": vault_file.get_public_url() or ""}
    return {"done": False}
