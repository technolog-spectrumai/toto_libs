"""
Backend base commands — they own execution.

* ``FfmpegCommand`` / ``FfprobeCommand`` stage inputs, run the argv from
  ``build_spec`` via subprocess, save outputs and serialize the result.
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import tempfile

from django.conf import settings
from django.core.files.base import File
from django.utils import timezone

from ..client import validate_argv
from ..models import FileJob
from .base import BaseCommand


def _work_root() -> str:
    path = getattr(settings, "MANTA_WORK_ROOT", "/tmp/manta")
    os.makedirs(path, exist_ok=True)
    return path


def _stage(vault_file, tmpdir: str, name: str) -> str:
    dst = os.path.join(tmpdir, name)
    try:
        os.symlink(vault_file.file.path, dst)
    except OSError:
        shutil.copyfile(vault_file.file.path, dst)
    return name


def _save_output(job, src_path: str, filename: str):
    from toto.vault.models import VaultFile

    primary = VaultFile.objects.filter(pk=job.inputs[0]).first() if job.inputs else None
    owner = job.owner or (primary.owner if primary else None)
    mime, _ = mimetypes.guess_type(filename)
    vf = VaultFile(
        owner=owner,
        title=os.path.splitext(filename)[0],
        bucket=primary.bucket if primary else None,
        directory=primary.directory if primary else None,
        file_type=VaultFile.detect_type(mime or "", filename),
    )
    with open(src_path, "rb") as fh:
        vf.file.save(filename, File(fh), save=False)
    vf.save()
    return vf


class FfmpegCommand(BaseCommand):
    """Runs ffmpeg argv (one or more passes) and stores the produced file(s)."""

    backend = "ffmpeg"
    backend_label = "ffmpeg"

    def execute(self, job: FileJob) -> None:
        from toto.vault.models import VaultFile

        if job.is_terminal:
            # A redelivered task (broker visibility timeout) must not
            # resurrect a row the stuck-run sweeper already closed.
            return
        job.status = FileJob.Status.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at"])
        try:
            self._run(job, VaultFile)
        except Exception as exc:
            job.status = FileJob.Status.FAILED
            job.output = {"error": str(exc)}
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "output", "finished_at"])
            raise

    #: manta's form field names -> the operation's declared parameters.
    #: Only these cross; anything else a form carries stays on this host.
    _GEAR_PARAMS = ("width", "height", "x", "y", "fps", "quality", "bitrate",
                    "start_time", "end_time", "duration", "position",
                    "reencode", "output_name")

    def _run_in_gear(self, job, inputs, lease) -> None:
        """Stage the inputs, run the command in a runner, file the outputs.

        The command and its parameters travel as DECLARED VALUES, never as an
        argv: the runner rebuilds the command line from the same pure builders
        this module used to call directly. That is what makes the move safe —
        the argv is assembled on the trusted side either way, and the only
        thing that changed is which side of a container wall it happens on.
        """
        import os

        from toto.anastasia import jobs
        from toto.vault.storage_backends import read_file_bytes

        from .. import models as manta_models  # noqa: F401 - FileJob's app

        params = dict(job.params or {})
        staged = {}
        for index, vault_file in enumerate(inputs[:2]):
            extension = os.path.splitext(vault_file.file.name)[1]
            staged[f"input{index}{extension}"] = read_file_bytes(vault_file)
        names = list(staged)

        call = {"command": job.command, "input": names[0]}
        if len(names) > 1:
            call["second"] = names[1]
        for key in self._GEAR_PARAMS:
            value = params.get(key)
            if value not in (None, ""):
                call[key] = value

        try:
            result = jobs.run(
                lease=lease, operation="run_media_command", params=call,
                inputs=staged, subject_label="manta.FileJob",
                subject_id=job.pk, requested_by=job.owner,
                timeout=int(params.get("time_budget_seconds") or 900))
        except jobs.JobFailed as exc:
            raise RuntimeError(
                str(exc) or "The media command failed in its Compute Gear"
            ) from exc

        output = {"command": f"{job.command} (in a Compute Gear)",
                  "outputs": [], "files": []}
        for name, data in sorted(result["outputs"].items()):
            path = os.path.join(_work_root(), f"job{job.pk}-{name}")
            os.makedirs(_work_root(), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(data)
            try:
                output["files"].append(_save_output(job, path, name).id)
                output["outputs"].append(name)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        if result["report"].get("duration_seconds") is not None:
            output["probe"] = {"duration": result["report"]["duration_seconds"]}

        job.output = output
        job.status = FileJob.Status.DONE
        job.finished_at = timezone.now()
        job.save(update_fields=["output", "status", "finished_at"])

    def _run(self, job, VaultFile) -> None:
        inputs = [VaultFile.objects.get(pk=i) for i in (job.inputs or [])]
        if not inputs:
            raise ValueError("Job has no input file.")

        # In a Compute Gear where the user has one; here where they do not and
        # this host still has ffmpeg. Zenobia has none since 1.50, so on that
        # host the first branch is the only one that runs — which is exactly
        # what lets it offer the command builder without the binary.
        lease = _gear_for(job)
        if lease is not None:
            return self._run_in_gear(job, inputs, lease)

        with tempfile.TemporaryDirectory(dir=_work_root()) as tmpdir:
            staged = [
                _stage(vf, tmpdir, f"input{i}{os.path.splitext(vf.file.name)[1]}")
                for i, vf in enumerate(inputs)
            ]
            if job.command == "concat":
                with open(os.path.join(tmpdir, "concat.txt"), "w") as fh:
                    for name in staged:
                        fh.write(f"file '{name}'\n")

            spec = self.build_spec(input_name=staged[0], extra_input_names=staged[1:], params=job.params)

            probe_stdout = None
            # The budget was snapshotted into params at dispatch, where the
            # celery limits were fixed — the subprocess ceiling must match
            # those, not a grant that changed since. The −60 puts the
            # subprocess timeout UNDER the soft limit, so TimeoutExpired
            # surfaces through the except-branch (clean FAILED + finished_at)
            # instead of racing SoftTimeLimitExceeded.
            budget = int((job.params or {}).get("time_budget_seconds") or 7200)
            for argv in spec.commands:
                validate_argv(list(argv))
                proc = subprocess.run(list(argv), cwd=tmpdir, capture_output=True,
                                      text=True, timeout=max(60, budget - 60))
                if proc.returncode != 0:
                    raise RuntimeError(f"{argv[0]} exited {proc.returncode}:\n{proc.stderr[-2000:]}")
                if self.backend == "ffprobe":
                    probe_stdout = proc.stdout

            output = {"command": spec.shell_display, "outputs": list(spec.output_names), "files": []}
            for out_name in spec.output_names:
                out_path = os.path.join(tmpdir, out_name)
                if self.backend == "ffprobe" and probe_stdout is not None:
                    content = probe_stdout
                    try:
                        parsed = json.loads(probe_stdout)
                        content = json.dumps(parsed, indent=2)
                        output["probe"] = parsed
                    except Exception:
                        pass
                    with open(out_path, "w", encoding="utf-8") as fh:
                        fh.write(content)
                if os.path.exists(out_path):
                    output["files"].append(_save_output(job, out_path, out_name).id)

            job.output = output
            job.status = FileJob.Status.DONE
            job.finished_at = timezone.now()
            job.save(update_fields=["output", "status", "finished_at"])


class FfprobeCommand(FfmpegCommand):
    backend = "ffprobe"
    backend_label = "ffprobe"
    tab = "ffprobe"


def _gear_for(job):
    """The Gear this job runs in, or None to run here.

    None on a host with no toto.anastasia — the whole of the old world — so
    this module keeps one code path for "there is nowhere else".
    """
    from django.apps import apps

    if not apps.is_installed("toto.anastasia"):
        return None
    from toto.anastasia import jobs

    try:
        return jobs.require_gear(job.owner)
    except jobs.NoGear:
        import shutil

        if shutil.which("ffmpeg"):
            return None          # this host can still do it itself
        raise
