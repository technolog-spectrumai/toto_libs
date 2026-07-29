"""
Backend base commands — they own execution.

* ``FfmpegCommand`` / ``FfprobeCommand`` stage inputs, run the argv from
  ``build_spec`` via subprocess, save outputs and serialize the result.
* ``WhisperCommand`` dispatches the existing fileservices ``FileServiceRun``
  (whisper) — the long-term home for this is manta, but for now we reuse that
  backend.
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

        job.status = FileJob.Status.RUNNING
        job.save(update_fields=["status"])
        try:
            self._run(job, VaultFile)
        except Exception as exc:
            job.status = FileJob.Status.FAILED
            job.output = {"error": str(exc)}
            job.save(update_fields=["status", "output"])
            raise

    def _run(self, job, VaultFile) -> None:
        inputs = [VaultFile.objects.get(pk=i) for i in (job.inputs or [])]
        if not inputs:
            raise ValueError("Job has no input file.")

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
            for argv in spec.commands:
                validate_argv(list(argv))
                proc = subprocess.run(list(argv), cwd=tmpdir, capture_output=True, text=True, timeout=7200)
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
            job.save(update_fields=["output", "status"])


class FfprobeCommand(FfmpegCommand):
    backend = "ffprobe"
    backend_label = "ffprobe"
    tab = "ffprobe"


class ServiceCommand(BaseCommand):
    """Dispatches a fileservices FileServiceRun (whisper / tesseract)."""

    backend = "service"

    def execute(self, job: FileJob) -> None:
        from toto.vault.models import VaultFile
        from toto.fileservices.dispatch import create_service_run, dispatch_run

        vf = VaultFile.objects.get(pk=job.inputs[0])
        args = (job.params or {}).get("language", "")
        run = create_service_run(job.owner, vf, self.service_key, args)
        job.celery_task_id = str(run.id)
        job.output = {"service_run_id": run.id}
        job.status = FileJob.Status.RUNNING
        job.save(update_fields=["celery_task_id", "output", "status"])
        dispatch_run(run)


class WhisperCommand(ServiceCommand):
    backend_label = "whisper"
    service_key = "transcription"
    tab = "transcribe"
