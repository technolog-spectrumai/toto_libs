from __future__ import annotations

import os
import shlex
import subprocess
import tempfile

from django.core.files.base import File
from django.utils import timezone

# Shell tokens rejected from user-supplied free-text args (shell=False, but we
# stay defensive). `;` is allowed for ffmpeg filtergraph chains.
_SHELL_TOKENS = ("&&", "||", "|", ">", "<", "`", "$(", "\n")


def tokenize_args(raw: str) -> list[str]:
    """Split free-text args into an argv list, rejecting shell metacharacters."""
    for token in _SHELL_TOKENS:
        if token in raw:
            raise ValueError(f"Rejected shell token {token!r} in arguments.")
    try:
        return shlex.split(raw)
    except ValueError as exc:
        raise ValueError(f"Could not parse arguments: {exc}") from exc


def stage_input(vault_file, tmpdir: str) -> str:
    """Copy a VaultFile's bytes into *tmpdir*, returning the local path."""
    name = os.path.basename(vault_file.file.name) or "input"
    local = os.path.join(tmpdir, name)
    with vault_file.file.open("rb") as src, open(local, "wb") as dst:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            dst.write(chunk)
    return local


def save_output(run, local_path: str, filename: str, file_type: str):
    """Persist a produced local file as a VaultFile beside the input file."""
    from toto.vault.models import VaultFile

    input_file = run.input_file
    vf = VaultFile(
        owner=run.owner,
        title=filename,
        bucket=input_file.bucket if input_file else None,
        directory=input_file.directory if input_file else None,
        file_type=file_type,
        is_public=False,
    )
    with open(local_path, "rb") as fh:
        vf.file.save(filename, File(fh), save=False)
    vf.save()
    try:
        vf.content_hash = vf.create_hash()
        vf.save(update_fields=["content_hash"])
    except Exception:
        pass
    return vf


def _sync_manta_job(run) -> None:
    """Serialize a finished FileServiceRun into its linked manta FileJob (if any)."""
    from .models import FileServiceRun
    try:
        from toto.manta.models import FileJob
    except Exception:
        return
    job = FileJob.objects.filter(output__service_run_id=run.id).first()
    if job is None:
        return
    if run.status == FileServiceRun.SUCCESS:
        job.status = FileJob.Status.DONE
        job.output = {"service_run_id": run.id, "files": list(run.output_file_pks or []),
                      "stdout": (run.stdout or "")[:4000]}
    else:
        job.status = FileJob.Status.FAILED
        job.output = {"service_run_id": run.id, "error": (run.stderr or "")[:4000]}
    job.save(update_fields=["status", "output"])


def execute_run(run_id: int) -> dict:
    """Load the plugin for a FileServiceRun and execute it, tracking status."""
    from .models import FileServiceRun
    from .plugin import FileServicePlugin

    run = FileServiceRun.objects.select_related("input_file", "owner", "bucket").get(pk=run_id)
    run.status = FileServiceRun.RUNNING
    run.save(update_fields=["status"])

    plugin = FileServicePlugin.get(run.service_key)
    if plugin is None:
        run.status = FileServiceRun.FAILED
        run.stderr = f"Unknown service: {run.service_key!r}"
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "stderr", "finished_at"])
        raise ValueError(run.stderr)

    try:
        output_pks = plugin.execute(run) or []
        run.output_file_pks = list(output_pks)
        run.status = FileServiceRun.SUCCESS
        run.finished_at = timezone.now()
        run.save(update_fields=["output_file_pks", "stdout", "stderr", "status", "finished_at"])
        _sync_manta_job(run)
        return {"run_id": run.id, "status": run.status, "output_file_pks": run.output_file_pks}
    except Exception as exc:
        run.status = FileServiceRun.FAILED
        if not run.stderr:
            run.stderr = str(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "stdout", "stderr", "finished_at"])
        _sync_manta_job(run)
        raise


# ----------------------------------------------------------------------
# Shared ffmpeg/ffprobe helpers used by the bundled plugins.
# ----------------------------------------------------------------------

def run_subprocess(argv: list[str], cwd: str, timeout: int = 7200) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=timeout,
    )
