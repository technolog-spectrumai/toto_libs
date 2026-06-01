import json
import os
import tempfile

from django.conf import settings
from django.core.files.base import File
from django.utils import timezone

from .client import FFmpegClient
from .models import MediaJob, ProbeResult
from . import builders


def _work_root() -> str:
    path = getattr(settings, "VIDEOMANT_WORK_ROOT", "/tmp/videomant")
    os.makedirs(path, exist_ok=True)
    return path


def _inject_progress_flags(argv: list[str]) -> list[str]:
    """Insert -progress pipe:1 -nostats before the last element (output path)."""
    return argv[:-1] + ["-progress", "pipe:1", "-nostats"] + [argv[-1]]


def _parse_probe_data(raw: dict) -> dict:
    fmt = raw.get("format", {})
    streams = raw.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    duration = None
    raw_dur = fmt.get("duration") or video.get("duration")
    if raw_dur:
        try:
            duration = float(raw_dur)
        except (ValueError, TypeError):
            pass
    return {
        "raw": raw,
        "format": fmt,
        "streams": streams,
        "duration_seconds": duration,
        "width": video.get("width"),
        "height": video.get("height"),
        "video_codec": video.get("codec_name", ""),
        "audio_codec": audio.get("codec_name", ""),
    }


def _save_vault_file(job: MediaJob, path: str, filename: str, content_type: str):
    from toto.vault.models import VaultFile

    owner = job.owner or (job.input_file.owner if job.input_file else None)
    if owner is None:
        raise RuntimeError("Cannot save VaultFile: no owner resolved for MediaJob")

    bucket = job.input_file.bucket if job.input_file else None
    directory = job.input_file.directory if job.input_file else None
    file_type = VaultFile.detect_type(content_type)

    vf = VaultFile(
        owner=owner,
        title=os.path.splitext(filename)[0],
        bucket=bucket,
        directory=directory,
        file_type=file_type,
    )
    with open(path, "rb") as fh:
        vf.file.save(filename, File(fh), save=False)
    vf.save()
    return vf


def _probe_file(input_path: str) -> dict | None:
    client = FFmpegClient()
    argv = builders.build_probe(input_path)
    result = client.run_probe(argv, timeout=30)
    if result.returncode != 0:
        return None
    try:
        return _parse_probe_data(json.loads(result.stdout))
    except Exception:
        return None


def _probe_duration(input_path: str) -> float | None:
    parsed = _probe_file(input_path)
    return parsed["duration_seconds"] if parsed else None


class MediaJobRunner:
    def run(self, job: MediaJob) -> None:
        job.status = MediaJob.Status.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at"])

        try:
            self._execute(job)
        except Exception as exc:
            job.status = MediaJob.Status.FAILED
            job.error_message = str(exc)
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error_message", "finished_at"])
            raise

    def _execute(self, job: MediaJob) -> None:
        client = FFmpegClient()

        with tempfile.TemporaryDirectory(dir=_work_root()) as tmpdir:
            if job.task_name == "videomant.probe":
                self._run_probe(job, client)
                return

            input_path = job.input_file.file.path
            probe = _probe_file(input_path)
            duration = probe["duration_seconds"] if probe else None
            if job.task_name == "videomant.concat":
                duration = self._concat_duration(job)
            if job.task_name == "videomant.extract_mp3":
                if not probe or not probe.get("audio_codec"):
                    raise ValueError("Input file has no audio stream — cannot extract MP3.")

            def on_progress(pct: int, msg: str) -> None:
                job.progress_percent = pct
                job.progress_message = msg
                job.save(update_fields=["progress_percent", "progress_message"])

            if job.task_name == "videomant.gif":
                self._run_gif(job, client, tmpdir, duration, on_progress)
            else:
                argv, out_path, filename, content_type = self._build(job, tmpdir)
                argv_with_progress = _inject_progress_flags(argv)
                job.rendered_argv = argv_with_progress
                job.save(update_fields=["rendered_argv"])

                result = client.run(
                    argv_with_progress,
                    timeout=7200,
                    progress_callback=on_progress if duration else None,
                    duration_seconds=duration,
                )
                job.stdout = result.stdout
                job.stderr = result.stderr
                job.exit_code = result.returncode
                if result.returncode != 0:
                    job.save(update_fields=["stdout", "stderr", "exit_code"])
                    raise RuntimeError(f"ffmpeg exited {result.returncode}:\n{result.stderr[-2000:]}")

                if out_path and os.path.exists(out_path):
                    vf = _save_vault_file(job, out_path, filename, content_type)
                    job.output_file = vf

            job.status = MediaJob.Status.SUCCEEDED
            job.progress_percent = 100
            job.finished_at = timezone.now()
            job.duration_seconds = (job.finished_at - job.started_at).total_seconds()
            job.save(update_fields=[
                "stdout", "stderr", "exit_code", "output_file",
                "status", "progress_percent", "finished_at", "duration_seconds",
            ])

    def _run_probe(self, job: MediaJob, client: FFmpegClient) -> None:
        job.progress_percent = 0
        job.save(update_fields=["progress_percent"])

        input_path = job.input_file.file.path
        argv = builders.build_probe(input_path)
        job.rendered_argv = argv
        job.save(update_fields=["rendered_argv"])

        result = client.run_probe(argv, timeout=60)
        job.stdout = result.stdout
        job.stderr = result.stderr
        job.exit_code = result.returncode

        if result.returncode != 0:
            job.save(update_fields=["stdout", "stderr", "exit_code"])
            raise RuntimeError(f"ffprobe exited {result.returncode}:\n{result.stderr}")

        try:
            raw = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ffprobe output is not valid JSON: {exc}") from exc

        parsed = _parse_probe_data(raw)
        probe = ProbeResult(
            vault_file=job.input_file,
            job=job,
            **{k: parsed[k] for k in ("raw", "format", "streams", "duration_seconds", "width", "height", "video_codec", "audio_codec")},
        )
        probe.save()

        job.output_metadata = parsed
        job.status = MediaJob.Status.SUCCEEDED
        job.progress_percent = 100
        job.finished_at = timezone.now()
        job.duration_seconds = (job.finished_at - job.started_at).total_seconds()
        job.save(update_fields=[
            "stdout", "stderr", "exit_code", "output_metadata",
            "status", "progress_percent", "finished_at", "duration_seconds",
        ])

    def _run_gif(self, job: MediaJob, client: FFmpegClient, tmpdir: str, duration: float | None, on_progress) -> None:
        params = job.params or {}
        input_path = job.input_file.file.path
        start_time = params.get("start_time", "00:00:00")
        gif_duration = str(params.get("duration", "5"))
        fps = int(params.get("fps", 12))
        width = int(params.get("width", 480))
        output_name = params.get("output_name", "output")

        palette_path = os.path.join(tmpdir, "palette.png")
        output_path = os.path.join(tmpdir, f"{output_name}.gif")

        palette_argv = builders.build_gif_palette(input_path, palette_path, start_time, gif_duration, fps, width)
        job.rendered_argv = [palette_argv]
        job.save(update_fields=["rendered_argv"])
        r1 = client.run_probe(palette_argv, timeout=300)
        if r1.returncode != 0:
            raise RuntimeError(f"GIF palette pass failed:\n{r1.stderr[-2000:]}")

        gif_argv = builders.build_gif(input_path, palette_path, output_path, start_time, gif_duration, fps, width)
        argv_with_progress = _inject_progress_flags(gif_argv)
        job.rendered_argv = [palette_argv, argv_with_progress]
        job.save(update_fields=["rendered_argv"])

        result = client.run(
            argv_with_progress,
            timeout=600,
            progress_callback=on_progress if duration else None,
            duration_seconds=duration,
        )
        job.stdout = result.stdout
        job.stderr = result.stderr
        job.exit_code = result.returncode
        if result.returncode != 0:
            raise RuntimeError(f"GIF encode pass failed:\n{result.stderr[-2000:]}")

        vf = _save_vault_file(job, output_path, f"{output_name}.gif", "image/gif")
        job.output_file = vf

    def _concat_duration(self, job: MediaJob) -> float | None:
        from toto.vault.models import VaultFile
        ids = job.input_files or []
        total = 0.0
        for fid in ids:
            try:
                vf = VaultFile.objects.get(pk=fid)
                d = _probe_duration(vf.file.path)
                if d:
                    total += d
            except Exception:
                pass
        return total if total > 0 else None

    def _build(self, job: MediaJob, tmpdir: str) -> tuple[list[str], str | None, str, str]:
        params = job.params or {}
        input_path = job.input_file.file.path
        output_name = params.get("output_name", "output")

        if job.task_name == "videomant.compress":
            out = os.path.join(tmpdir, f"{output_name}.mp4")
            return builders.build_compress(input_path, out, params.get("quality", "medium")), out, f"{output_name}.mp4", "video/mp4"

        if job.task_name == "videomant.resize":
            out = os.path.join(tmpdir, f"{output_name}.mp4")
            return builders.build_resize(input_path, out, int(params.get("width", -2)), int(params.get("height", -2))), out, f"{output_name}.mp4", "video/mp4"

        if job.task_name == "videomant.cut":
            out = os.path.join(tmpdir, f"{output_name}.mp4")
            return builders.build_cut(input_path, out, params["start_time"], params["end_time"]), out, f"{output_name}.mp4", "video/mp4"

        if job.task_name == "videomant.extract_mp3":
            out = os.path.join(tmpdir, f"{output_name}.mp3")
            return builders.build_extract_mp3(input_path, out, params.get("bitrate", "192k")), out, f"{output_name}.mp3", "audio/mpeg"

        if job.task_name == "videomant.thumbnail":
            out = os.path.join(tmpdir, f"{output_name}.jpg")
            return builders.build_thumbnail(input_path, out, params.get("at_time", "00:00:01")), out, f"{output_name}.jpg", "image/jpeg"

        if job.task_name == "videomant.concat":
            from toto.vault.models import VaultFile
            ids = job.input_files or []
            concat_list = os.path.join(tmpdir, "concat.txt")
            with open(concat_list, "w") as fh:
                for fid in ids:
                    vf = VaultFile.objects.get(pk=fid)
                    fh.write(f"file '{vf.file.path}'\n")
            out = os.path.join(tmpdir, f"{output_name}.mp4")
            return builders.build_concat(concat_list, out, bool(params.get("reencode", False))), out, f"{output_name}.mp4", "video/mp4"

        raise ValueError(f"Unknown task_name: {job.task_name!r}")
