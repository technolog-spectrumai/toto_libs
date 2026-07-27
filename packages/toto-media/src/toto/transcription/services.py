from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import uuid as _uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.text import slugify

from .models import (
    TranscriptAccessMode,
    TranscriptArtifact,
    TranscriptCollection,
    TranscriptEvent,
    TranscriptionJob,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
)


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class SegmentResult:
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None
    speaker_label: str | None = None
    metadata: dict[str, Any] | None = None


def get_vault_models():
    try:
        return apps.get_model("vault", "Bucket"), apps.get_model("vault", "VaultFile")
    except LookupError as exc:
        raise RuntimeError("Install the existing vault app before toto.transcription.") from exc


def user_can_create_collections(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return True
    return bool(getattr(settings, "TRANSCRIPTION_ALLOW_USER_COLLECTIONS", True))


def user_is_transcription_manager(user, collection: TranscriptCollection | None = None) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    if collection is None:
        return user_can_create_collections(user)
    return collection.user_can_write(user)


def readable_collections_for_user(user):
    qs = TranscriptCollection.objects.select_related("owner", "bucket")
    if not user or not getattr(user, "is_authenticated", False):
        return qs.filter(access_mode=TranscriptAccessMode.PUBLIC)
    if getattr(user, "is_superuser", False):
        return qs
    return qs.filter(Q(access_mode=TranscriptAccessMode.PUBLIC) | Q(owner=user) | Q(readers=user) | Q(writers=user)).distinct()


def writable_collections_for_user(user):
    qs = TranscriptCollection.objects.select_related("owner", "bucket")
    if not user or not getattr(user, "is_authenticated", False):
        return qs.none()
    if getattr(user, "is_superuser", False):
        return qs
    return qs.filter(Q(owner=user) | Q(writers=user)).distinct()


def readable_sources_for_user(user):
    qs = TranscriptSource.objects.select_related("collection", "source_file")
    if not user or not getattr(user, "is_authenticated", False):
        return qs.filter(collection__access_mode=TranscriptAccessMode.PUBLIC, status=TranscriptSource.Status.TRANSCRIBED)
    if getattr(user, "is_superuser", False):
        return qs
    return qs.filter(
        Q(collection__access_mode=TranscriptAccessMode.PUBLIC, status=TranscriptSource.Status.TRANSCRIBED)
        | Q(collection__owner=user)
        | Q(collection__readers=user)
        | Q(collection__writers=user)
    ).distinct()


def can_access_source(user, source: TranscriptSource) -> AccessDecision:
    if source.status == TranscriptSource.Status.ARCHIVED:
        if user_is_transcription_manager(user, source.collection):
            return AccessDecision(True, "manager-archived")
        return AccessDecision(False, "archived")
    if user_is_transcription_manager(user, source.collection):
        return AccessDecision(True, "manager")
    if source.status != TranscriptSource.Status.TRANSCRIBED:
        return AccessDecision(False, "not-transcribed")
    if source.collection.user_can_read(user):
        return AccessDecision(True, source.effective_access_mode)
    if not user or not getattr(user, "is_authenticated", False):
        return AccessDecision(False, "login-required")
    return AccessDecision(False, "private")


def detect_upload_type(uploaded_file) -> str:
    _, VaultFile = get_vault_models()
    mime = (getattr(uploaded_file, "content_type", "") or "").lower()
    name = (getattr(uploaded_file, "name", "") or "").lower()
    detected = ""
    if hasattr(VaultFile, "detect_type"):
        detected = VaultFile.detect_type(mime)
    if detected in {"audio", "video"}:
        return detected
    if mime.startswith("audio/") or name.endswith((".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".webm")):
        return "audio"
    if mime.startswith("video/") or name.endswith((".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".ts", ".m2ts")):
        return "video"
    return detected or "text"


def get_or_create_transcription_bucket(*, owner, name: str = "Transcription"):
    Bucket, _ = get_vault_models()
    base_slug = slugify(f"{name}-{owner.pk if owner else 'system'}")[:120] or "transcription"
    bucket, _ = Bucket.objects.get_or_create(
        slug=base_slug,
        defaults={"name": f"{name} {owner.pk if owner else 'system'}", "owner": owner},
    )
    return bucket


@transaction.atomic
def create_vault_file_from_upload(*, uploaded_file, owner, bucket=None, title: str = "", make_public: bool = False):
    _, VaultFile = get_vault_models()
    bucket = bucket or get_or_create_transcription_bucket(owner=owner)
    file_type = detect_upload_type(uploaded_file)
    base_name, ext = os.path.splitext(os.path.basename(uploaded_file.name))
    candidate_key = slugify(base_name)
    if candidate_key and VaultFile.objects.filter(bucket=bucket, key=candidate_key).exists():
        uploaded_file.name = f"{base_name}-{_uuid.uuid4().hex[:8]}{ext}"
    vault_file = VaultFile.objects.create(
        owner=owner,
        title=title or os.path.splitext(os.path.basename(uploaded_file.name))[0],
        bucket=bucket,
        file=uploaded_file,
        file_type=file_type,
        is_public=make_public,
        is_encrypted=False,
    )
    if hasattr(vault_file, "create_hash") and not getattr(vault_file, "content_hash", ""):
        content_hash = vault_file.create_hash()
        if content_hash:
            vault_file.content_hash = content_hash
            vault_file.save(update_fields=["content_hash"])
    return vault_file


@transaction.atomic
def create_source_from_vault_file(*, collection: TranscriptCollection, vault_file, title: str, description: str = "", language: str = "", status: str = TranscriptSource.Status.DRAFT) -> TranscriptSource:
    if getattr(vault_file, "file_type", None) not in {"audio", "video"}:
        raise ValidationError("Selected vault file is not an audio or video file.")
    return TranscriptSource.objects.create(
        collection=collection,
        source_file=vault_file,
        title=title,
        description=description,
        language=language,
        status=status,
    )


@transaction.atomic
def create_source_from_upload(*, collection: TranscriptCollection, uploaded_file, owner, title: str, description: str = "", language: str = "", status: str = TranscriptSource.Status.DRAFT):
    source_file = create_vault_file_from_upload(
        uploaded_file=uploaded_file,
        owner=owner,
        bucket=collection.bucket,
        title=title,
        make_public=collection.access_mode == TranscriptAccessMode.PUBLIC,
    )
    if getattr(source_file, "file_type", None) not in {"audio", "video"}:
        raise ValidationError("Uploaded file is not a recognized audio or video file.")
    return TranscriptSource.objects.create(collection=collection, source_file=source_file, title=title, description=description, language=language, status=status)


def _file_to_local_path(vault_file) -> tuple[str, tempfile.TemporaryDirectory | None]:
    file_obj = getattr(vault_file, "file", None)
    if not file_obj:
        raise RuntimeError("Source file has no storage file.")
    try:
        path = file_obj.path
        if path and os.path.exists(path):
            return path, None
    except Exception:
        pass
    tmp = tempfile.TemporaryDirectory()
    suffix = Path(getattr(file_obj, "name", "source")).suffix or ".media"
    local_path = Path(tmp.name) / f"source{suffix}"
    with file_obj.open("rb") as fh, local_path.open("wb") as out:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            out.write(chunk)
    return str(local_path), tmp


def _coerce_segment(item: Any) -> SegmentResult:
    if isinstance(item, SegmentResult):
        return item
    if isinstance(item, str):
        return SegmentResult(start_ms=0, end_ms=0, text=item)
    if not isinstance(item, dict):
        raise TypeError(f"Unsupported segment item: {type(item)!r}")
    start = item.get("start_ms")
    end = item.get("end_ms")
    if start is None:
        start = int(float(item.get("start", 0)) * 1000)
    if end is None:
        end = int(float(item.get("end", 0)) * 1000)
    return SegmentResult(
        start_ms=int(start or 0),
        end_ms=int(end or 0),
        text=str(item.get("text", "")).strip(),
        confidence=item.get("confidence"),
        speaker_label=item.get("speaker") or item.get("speaker_label"),
        metadata={k: v for k, v in item.items() if k not in {"start", "end", "start_ms", "end_ms", "text", "confidence", "speaker", "speaker_label"}},
    )


def _normalize_backend_result(result: Any) -> tuple[list[SegmentResult], dict[str, Any]]:
    raw: dict[str, Any] = {}
    if isinstance(result, dict):
        raw = result
        source = result.get("segments") or ([result] if "text" in result else [])
    elif isinstance(result, (list, tuple)):
        source = result
    elif isinstance(result, str):
        source = [result]
    else:
        source = list(result or [])
    segments = [_coerce_segment(item) for item in source]
    return [s for s in segments if s.text], raw


def _run_callable_backend(job: TranscriptionJob, local_path: str):
    dotted = getattr(settings, "TRANSCRIPTION_BACKEND", "")
    if not dotted:
        raise RuntimeError("TRANSCRIPTION_BACKEND is not configured.")
    backend = import_string(dotted)
    return backend(local_path, job=job, language=job.language or job.source.language or "")


def _run_command_backend(job: TranscriptionJob, local_path: str):
    command = getattr(settings, "TRANSCRIPTION_COMMAND", None)
    if not command:
        raise RuntimeError("TRANSCRIPTION_COMMAND is not configured.")
    language = job.language or job.source.language or ""
    cmd = [str(part).format(file=local_path, language=language, prompt=job.prompt or "") for part in command]
    completed = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"Transcription command failed with exit code {completed.returncode}.")
    stdout = completed.stdout.strip()
    if not stdout:
        return []
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return stdout


def _whisper_task(job: TranscriptionJob) -> str:
    """Return the local Whisper task.

    Both openai-whisper and faster-whisper can translate speech to English with
    task="translate". They are not general-purpose speech-to-any-language
    translators, so reject non-English translate_to values instead of silently
    doing the wrong thing.
    """

    target = (job.translate_to or "").strip().lower()
    if not target:
        return "transcribe"
    if target in {"en", "eng", "english"}:
        return "translate"
    raise RuntimeError(
        "Local Whisper backends only support translation to English. "
        "Leave translate_to empty for transcription, or set it to 'en'."
    )


def _language_for_job(job: TranscriptionJob) -> str | None:
    return (job.language or job.source.language or "").strip() or None


def _run_openai_whisper_backend(job: TranscriptionJob, local_path: str):
    """Run the original openai-whisper package locally.

    Requires:
        pip install -U openai-whisper
        ffmpeg installed and available on PATH

    Settings:
        TRANSCRIPTION_OPENAI_WHISPER_MODEL = "small"
        TRANSCRIPTION_OPENAI_WHISPER_DEVICE = "cuda" | "cpu" | None
        TRANSCRIPTION_OPENAI_WHISPER_FP16 = True | False | None
        TRANSCRIPTION_WHISPER_DOWNLOAD_ROOT = "/srv/models/whisper"
            Cache directory for model weights. Pre-download once with:
            python -c "import whisper; whisper.load_model('small', download_root='/srv/models/whisper')"
        TRANSCRIPTION_WHISPER_LOCAL_FILES_ONLY = True
            Raise an error instead of downloading if the model is not cached.
    """

    try:
        import whisper
    except ImportError as exc:
        raise RuntimeError("Install openai-whisper: pip install -U openai-whisper") from exc

    model_name = getattr(
        settings,
        "TRANSCRIPTION_OPENAI_WHISPER_MODEL",
        getattr(settings, "TRANSCRIPTION_WHISPER_MODEL", "small"),
    )
    device = getattr(settings, "TRANSCRIPTION_OPENAI_WHISPER_DEVICE", None)
    fp16 = getattr(settings, "TRANSCRIPTION_OPENAI_WHISPER_FP16", None)
    download_root = getattr(settings, "TRANSCRIPTION_WHISPER_DOWNLOAD_ROOT", None)
    local_only = getattr(settings, "TRANSCRIPTION_WHISPER_LOCAL_FILES_ONLY", False)

    # Active model from DB (set via the Model Setup UI) overrides settings.
    active_path = _active_model_path("openai_whisper")
    if active_path:
        model_name = active_path
        load_kwargs: dict[str, Any] = {"device": device} if device else {}
        load_kwargs["download_root"] = active_path
    else:
        if local_only:
            import os
            os.environ["HF_HUB_OFFLINE"] = "1"
        load_kwargs = {}
        if device:
            load_kwargs["device"] = device
        if download_root:
            load_kwargs["download_root"] = download_root
    model = whisper.load_model(model_name, **load_kwargs)

    kwargs = {
        "language": _language_for_job(job),
        "task": _whisper_task(job),
        "initial_prompt": job.prompt or None,
        "verbose": False,
    }
    if fp16 is not None:
        kwargs["fp16"] = bool(fp16)

    result = model.transcribe(local_path, **kwargs)
    segments = []
    for item in result.get("segments", []) or []:
        segments.append({
            "start": item.get("start", 0),
            "end": item.get("end", 0),
            "text": item.get("text", ""),
            "confidence": item.get("avg_logprob"),
            "no_speech_prob": item.get("no_speech_prob"),
            "compression_ratio": item.get("compression_ratio"),
        })
    return {
        "backend": "openai-whisper",
        "model": model_name,
        "language": result.get("language") or _language_for_job(job) or "",
        "task": kwargs["task"],
        "text": result.get("text", ""),
        "segments": segments,
    }


def _run_faster_whisper_backend(job: TranscriptionJob, local_path: str):
    """Run faster-whisper locally.

    Requires:
        pip install faster-whisper

    Settings:
        TRANSCRIPTION_FASTER_WHISPER_MODEL = "small"
            Model name ("tiny", "base", "small", "medium", "large-v3") or an
            absolute path to a locally exported CTranslate2 model directory.
        TRANSCRIPTION_FASTER_WHISPER_DEVICE = "cpu" | "cuda"
        TRANSCRIPTION_FASTER_WHISPER_COMPUTE_TYPE = "int8" | "float16" | "float32"
        TRANSCRIPTION_FASTER_WHISPER_BEAM_SIZE = 5
        TRANSCRIPTION_WHISPER_DOWNLOAD_ROOT = "/srv/models/faster-whisper"
            Directory where downloaded model weights are cached. Pre-download once:
            python -c "from faster_whisper import WhisperModel; WhisperModel('small', download_root='/srv/models/faster-whisper')"
        TRANSCRIPTION_WHISPER_LOCAL_FILES_ONLY = True
            Never contact HuggingFace Hub — raise immediately if model not cached.
    """

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Install faster-whisper: pip install faster-whisper") from exc

    model_name = getattr(
        settings,
        "TRANSCRIPTION_FASTER_WHISPER_MODEL",
        getattr(settings, "TRANSCRIPTION_WHISPER_MODEL", "small"),
    )
    device = getattr(settings, "TRANSCRIPTION_FASTER_WHISPER_DEVICE", "cpu")
    compute_type = getattr(settings, "TRANSCRIPTION_FASTER_WHISPER_COMPUTE_TYPE", "int8")
    beam_size = int(getattr(settings, "TRANSCRIPTION_FASTER_WHISPER_BEAM_SIZE", 5))
    download_root = getattr(settings, "TRANSCRIPTION_WHISPER_DOWNLOAD_ROOT", None)
    local_only = getattr(settings, "TRANSCRIPTION_WHISPER_LOCAL_FILES_ONLY", False)

    # Active model from DB (set via the Model Setup UI) overrides settings.
    active_path = _active_model_path("faster_whisper")
    if active_path:
        model_name = active_path
        model_kwargs: dict[str, Any] = {"device": device, "compute_type": compute_type, "local_files_only": True}
    else:
        model_kwargs = {"device": device, "compute_type": compute_type}
        if download_root:
            model_kwargs["download_root"] = download_root
        if local_only:
            model_kwargs["local_files_only"] = True

    model = WhisperModel(model_name, **model_kwargs)
    language = _language_for_job(job)
    task = _whisper_task(job)
    segments_iter, info = model.transcribe(
        local_path,
        language=language,
        task=task,
        initial_prompt=job.prompt or None,
        beam_size=beam_size,
    )
    segments = []
    for segment in segments_iter:
        segments.append({
            "start": segment.start,
            "end": segment.end,
            "text": segment.text,
            "confidence": getattr(segment, "avg_logprob", None),
        })
    return {
        "backend": "faster-whisper",
        "model": model_name,
        "device": device,
        "compute_type": compute_type,
        "language": getattr(info, "language", language or "") or "",
        "language_probability": getattr(info, "language_probability", None),
        "duration": getattr(info, "duration", None),
        "task": task,
        "segments": segments,
    }


def run_backend(job: TranscriptionJob, local_path: str):
    if job.engine == TranscriptionJob.Engine.OPENAI_WHISPER:
        return _run_openai_whisper_backend(job, local_path)
    if job.engine == TranscriptionJob.Engine.FASTER_WHISPER:
        return _run_faster_whisper_backend(job, local_path)
    if job.engine == TranscriptionJob.Engine.COMMAND:
        return _run_command_backend(job, local_path)
    if job.engine == TranscriptionJob.Engine.CUSTOM:
        return _run_callable_backend(job, local_path)

    default_engine = getattr(settings, "TRANSCRIPTION_DEFAULT_ENGINE", "faster_whisper")
    if default_engine == "openai_whisper":
        return _run_openai_whisper_backend(job, local_path)
    if default_engine == "faster_whisper":
        return _run_faster_whisper_backend(job, local_path)
    if default_engine == "custom" or getattr(settings, "TRANSCRIPTION_BACKEND", ""):
        return _run_callable_backend(job, local_path)
    if default_engine == "command" or getattr(settings, "TRANSCRIPTION_COMMAND", None):
        return _run_command_backend(job, local_path)
    raise RuntimeError(f"Unsupported TRANSCRIPTION_DEFAULT_ENGINE: {default_engine!r}")


@transaction.atomic
def create_transcription_job(*, source: TranscriptSource, user=None, engine: str = TranscriptionJob.Engine.DEFAULT, language: str = "", detect_speakers: bool = False, prompt: str = "") -> TranscriptionJob:
    job = TranscriptionJob.objects.create(source=source, engine=engine, language=language or source.language, detect_speakers=detect_speakers, prompt=prompt)
    source.status = TranscriptSource.Status.QUEUED
    source.save(update_fields=["status", "updated_at"])
    return job


def run_transcription(job: TranscriptionJob) -> TranscriptionJob:
    source = job.source
    job.status = TranscriptionJob.Status.RUNNING
    job.started_at = timezone.now()
    job.error_message = ""
    job.save(update_fields=["status", "started_at", "error_message", "updated_at"])
    source.status = TranscriptSource.Status.PROCESSING
    source.save(update_fields=["status", "updated_at"])
    tmp = None
    try:
        local_path, tmp = _file_to_local_path(source.source_file)
        result = run_backend(job, local_path)
        segments, raw = _normalize_backend_result(result)
        transcript_text = "\n".join(s.text for s in segments).strip()
        with transaction.atomic():
            source.segments.all().delete()
            job.speakers.all().delete()
            for idx, segment in enumerate(segments, start=1):
                speaker = None
                if segment.speaker_label:
                    speaker, _ = TranscriptSpeaker.objects.get_or_create(job=job, label=segment.speaker_label)
                TranscriptSegment.objects.create(
                    job=job,
                    source=source,
                    speaker=speaker,
                    index=idx,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    text=segment.text,
                    confidence=segment.confidence,
                    metadata=segment.metadata or {},
                )
            job.status = TranscriptionJob.Status.SUCCESS
            job.finished_at = timezone.now()
            job.raw_response = raw
            job.save(update_fields=["status", "finished_at", "raw_response", "updated_at"])
            source.status = TranscriptSource.Status.TRANSCRIBED
            source.transcript_text = transcript_text
            source.segments_count = len(segments)
            source.transcribed_at = timezone.now()
            source.save(update_fields=["status", "transcript_text", "segments_count", "transcribed_at", "updated_at"])
        return job
    except Exception as exc:
        job.status = TranscriptionJob.Status.FAILED
        job.finished_at = timezone.now()
        job.error_message = str(exc)
        job.save(update_fields=["status", "finished_at", "error_message", "updated_at"])
        source.status = TranscriptSource.Status.FAILED
        source.save(update_fields=["status", "updated_at"])
        raise
    finally:
        if tmp is not None:
            tmp.cleanup()


def run_transcription_job(job_id: int) -> dict[str, Any]:
    job = TranscriptionJob.objects.select_related("source", "source__source_file", "source__collection").get(pk=job_id)
    run_transcription(job)
    return {"job_id": job.pk, "status": job.status, "source_id": job.source_id}


def _active_speech_model(backend: str):
    """Return the active SpeechModel for *backend*, or None."""
    try:
        from .models import SpeechModel
        return SpeechModel.objects.filter(backend=backend, is_active=True).first()
    except Exception:
        return None


def celery_workers_available(timeout: float = 1.0) -> bool:
    """Return True if at least one Celery worker responds within *timeout* seconds."""
    try:
        from celery import current_app
        responses = current_app.control.inspect(timeout=timeout).ping()
        return bool(responses)
    except Exception:
        return False


def run_transcription_with_timeout(job: TranscriptionJob, timeout_seconds: int) -> TranscriptionJob:
    """Run transcription synchronously but abort after *timeout_seconds*.

    The underlying thread is allowed to finish naturally (Python threads cannot be
    forcibly killed), but the job is marked FAILED immediately so the caller gets
    a timely response.
    """
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_transcription, job)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            job_fresh = TranscriptionJob.objects.get(pk=job.pk)
            if job_fresh.status not in {TranscriptionJob.Status.SUCCESS, TranscriptionJob.Status.FAILED, TranscriptionJob.Status.CANCELLED}:
                job_fresh.status = TranscriptionJob.Status.FAILED
                job_fresh.finished_at = timezone.now()
                job_fresh.error_message = f"Timed out after {timeout_seconds}s."
                job_fresh.save(update_fields=["status", "finished_at", "error_message", "updated_at"])
                TranscriptSource.objects.filter(pk=job_fresh.source_id, status=TranscriptSource.Status.PROCESSING).update(status=TranscriptSource.Status.FAILED)
            raise TimeoutError(f"Transcription timed out after {timeout_seconds}s.")


def transcribe_demo_file(audio_path: str, *, engine: str = TranscriptionJob.Engine.DEFAULT, language: str = "") -> dict[str, Any]:
    """Transcribe a local file path without persisting any DB rows.

    Returns a dict with keys: text (str), segments (list of dicts), engine (str).
    Raises RuntimeError on failure.
    """
    job = TranscriptionJob(engine=engine, language=language)
    job.translate_to = ""
    job.prompt = ""
    # Attach a dummy source for language lookup used inside backends.
    dummy_source = TranscriptSource(language=language)
    job.source = dummy_source

    result = run_backend(job, audio_path)
    segments, _ = _normalize_backend_result(result)
    text = "\n".join(s.text for s in segments).strip()
    return {
        "text": text,
        "segments": [{"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text} for s in segments],
        "engine": engine,
    }


def cancel_job(job_pk: int, *, user=None) -> TranscriptionJob:
    """Mark a queued or running job as CANCELLED and revoke the Celery task if known."""
    job = TranscriptionJob.objects.select_related("source").get(pk=job_pk)
    cancellable = {TranscriptionJob.Status.QUEUED, TranscriptionJob.Status.RUNNING}
    if job.status not in cancellable:
        raise ValueError(f"Job {job_pk} is {job.status} and cannot be cancelled.")
    if job.celery_task_id:
        try:
            from celery import current_app
            current_app.control.revoke(job.celery_task_id, terminate=True, signal="SIGTERM")
        except Exception:
            pass
    job.status = TranscriptionJob.Status.CANCELLED
    job.finished_at = timezone.now()
    job.error_message = f"Cancelled by {user}." if user else "Cancelled."
    job.save(update_fields=["status", "finished_at", "error_message", "updated_at"])
    source = job.source
    still_active = source.jobs.filter(status__in=[TranscriptionJob.Status.QUEUED, TranscriptionJob.Status.RUNNING]).exclude(pk=job.pk).exists()
    if not still_active and source.status in {TranscriptSource.Status.QUEUED, TranscriptSource.Status.PROCESSING}:
        source.status = TranscriptSource.Status.DRAFT
        source.save(update_fields=["status", "updated_at"])
    return job


def activate_speech_model(model_pk: int) -> None:
    """Mark this SpeechModel as active, deactivating others with the same backend."""
    from .models import SpeechModel
    m = SpeechModel.objects.get(pk=model_pk)
    SpeechModel.objects.filter(backend=m.backend).update(is_active=False)
    SpeechModel.objects.filter(pk=model_pk).update(is_active=True)


def start_speech_model_download(model_pk: int, download_source: str) -> None:
    """Validate and queue the Celery download task for a SpeechModel."""
    from .models import SpeechModel
    m = SpeechModel.objects.get(pk=model_pk)
    if m.download_status == SpeechModel.DownloadStatus.DOWNLOADING:
        raise ValueError("A download is already in progress for this model.")
    source = (download_source or "").strip()
    if not source:
        raise ValueError("Provide a download source (HuggingFace repo ID, size name, or URL).")
    SpeechModel.objects.filter(pk=model_pk).update(
        download_source=source,
        download_status=SpeechModel.DownloadStatus.DOWNLOADING,
        download_progress=0,
        download_error="",
    )
    from .tasks import download_speech_model
    result = download_speech_model.delay(model_pk)
    SpeechModel.objects.filter(pk=model_pk).update(celery_task_id=result.id)


_SIZE_NAMES = {"tiny", "base", "small", "medium", "large", "large-v1", "large-v2", "large-v3"}


def download_speech_model_weights(model_pk: int) -> None:
    """Download model weights and persist them in the SpeechModel.weights_file field."""
    import io
    import threading
    import urllib.request
    import zipfile

    from django.core.files.base import ContentFile as DjContentFile

    from .models import SpeechModel

    m = SpeechModel.objects.get(pk=model_pk)
    source = m.download_source.strip()

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            stop_event = threading.Event()

            def _watch_dir(watch_path: Path):
                while not stop_event.is_set():
                    try:
                        current = sum(f.stat().st_size for f in watch_path.rglob("*") if f.is_file())
                        pct = min(90, max(1, int(current / (1024 * 1024))))
                        SpeechModel.objects.filter(pk=model_pk).update(download_progress=pct)
                    except Exception:
                        pass
                    stop_event.wait(2.0)

            watcher = threading.Thread(target=_watch_dir, args=(tmp_path,), daemon=True)
            watcher.start()

            try:
                if source.startswith("https://") or source.startswith("http://"):
                    # Direct URL download
                    fname = source.rsplit("/", 1)[-1] or "model.bin"
                    dest = tmp_path / fname
                    buf = io.BytesIO()
                    with urllib.request.urlopen(source) as resp:
                        while True:
                            chunk = resp.read(1024 * 1024)
                            if not chunk:
                                break
                            buf.write(chunk)
                    dest.write_bytes(buf.getvalue())
                    stop_event.set()
                    field_name = fname
                    file_content = dest.read_bytes()
                elif m.backend == SpeechModel.Backend.FASTER_WHISPER:
                    # HuggingFace snapshot (CTranslate2 directory → zip)
                    try:
                        from huggingface_hub import snapshot_download
                    except ImportError as exc:
                        raise RuntimeError("Install huggingface_hub: pip install huggingface-hub") from exc
                    repo_id = source if "/" in source else f"Systran/faster-whisper-{source}"
                    model_dir = tmp_path / "model"
                    model_dir.mkdir()
                    snapshot_download(
                        repo_id=repo_id,
                        local_dir=str(model_dir),
                        ignore_patterns=["*.msgpack", "*.h5", "flax_model*", "tf_model*", "rust_model*"],
                    )
                    stop_event.set()
                    zip_buf = io.BytesIO()
                    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                        for f in model_dir.rglob("*"):
                            if f.is_file():
                                zf.write(f, f.relative_to(model_dir))
                    slug = source.replace("/", "_")
                    field_name = f"{slug}.zip"
                    file_content = zip_buf.getvalue()
                else:
                    # openai-whisper .pt download
                    try:
                        import whisper
                    except ImportError as exc:
                        raise RuntimeError("Install openai-whisper: pip install -U openai-whisper") from exc
                    whisper.load_model(source, download_root=str(tmp_path))
                    stop_event.set()
                    pt_files = list(tmp_path.glob("*.pt"))
                    if not pt_files:
                        raise RuntimeError("No .pt file found after openai-whisper download.")
                    pt_file = pt_files[0]
                    field_name = pt_file.name
                    file_content = pt_file.read_bytes()
            finally:
                stop_event.set()

            m.refresh_from_db()
            m.weights_file.save(field_name, DjContentFile(file_content), save=False)
            m.download_status = SpeechModel.DownloadStatus.DONE
            m.download_progress = 100
            m.download_error = ""
            m.save(update_fields=["weights_file", "download_status", "download_progress", "download_error", "updated_at"])

    except Exception as exc:
        SpeechModel.objects.filter(pk=model_pk).update(
            download_status=SpeechModel.DownloadStatus.FAILED,
            download_error=str(exc)[:1000],
        )
        raise


def ms_to_srt_time(ms: int) -> str:
    seconds, milli = divmod(int(ms or 0), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milli:03}"


def ms_to_vtt_time(ms: int) -> str:
    return ms_to_srt_time(ms).replace(",", ".")


def transcript_to_text(source: TranscriptSource) -> str:
    lines = []
    for segment in source.segments.select_related("speaker").all():
        speaker = f"{segment.speaker.label}: " if segment.speaker_id else ""
        timestamp = f"[{segment.start_label}] " if segment.start_ms or segment.end_ms else ""
        lines.append(f"{timestamp}{speaker}{segment.text}")
    return "\n".join(lines).strip() or source.transcript_text


def transcript_to_srt(source: TranscriptSource) -> str:
    blocks = []
    for idx, segment in enumerate(source.segments.all(), start=1):
        blocks.append(f"{idx}\n{ms_to_srt_time(segment.start_ms)} --> {ms_to_srt_time(segment.end_ms)}\n{segment.text}\n")
    return "\n".join(blocks).strip()


def transcript_to_vtt(source: TranscriptSource) -> str:
    blocks = ["WEBVTT", ""]
    for segment in source.segments.all():
        blocks.append(f"{ms_to_vtt_time(segment.start_ms)} --> {ms_to_vtt_time(segment.end_ms)}")
        blocks.append(segment.text)
        blocks.append("")
    return "\n".join(blocks).strip() + "\n"


def transcript_to_json(source: TranscriptSource) -> str:
    payload = {"source": {"id": source.pk, "title": source.title, "slug": source.slug, "language": source.language, "duration_seconds": source.duration_seconds}, "segments": []}
    for s in source.segments.select_related("speaker").all():
        payload["segments"].append({"index": s.index, "start_ms": s.start_ms, "end_ms": s.end_ms, "speaker": s.speaker.label if s.speaker_id else None, "text": s.text, "confidence": s.confidence, "metadata": s.metadata})
    return json.dumps(payload, ensure_ascii=False, indent=2)


def export_transcript(source: TranscriptSource, kind: str) -> tuple[str, str, str]:
    kind = kind.lower()
    if kind == TranscriptArtifact.Kind.TXT:
        return transcript_to_text(source), "text/plain; charset=utf-8", f"{source.slug}.txt"
    if kind == TranscriptArtifact.Kind.SRT:
        return transcript_to_srt(source), "application/x-subrip; charset=utf-8", f"{source.slug}.srt"
    if kind == TranscriptArtifact.Kind.VTT:
        return transcript_to_vtt(source), "text/vtt; charset=utf-8", f"{source.slug}.vtt"
    if kind == TranscriptArtifact.Kind.JSON:
        return transcript_to_json(source), "application/json; charset=utf-8", f"{source.slug}.json"
    raise ValidationError(f"Unsupported transcript export kind: {kind}")


@transaction.atomic
def save_transcript_artifact(*, source: TranscriptSource, kind: str, created_by=None) -> TranscriptArtifact:
    content, content_type, filename = export_transcript(source, kind)
    _, VaultFile = get_vault_models()
    bucket = source.collection.bucket or get_or_create_transcription_bucket(owner=created_by or source.collection.owner)
    vault_file = VaultFile.objects.create(
        owner=created_by or source.collection.owner,
        title=f"{source.title} {kind.upper()}",
        bucket=bucket,
        file=ContentFile(content.encode("utf-8"), name=filename),
        file_type="text",
        is_public=source.collection.access_mode == TranscriptAccessMode.PUBLIC,
        is_encrypted=False,
    )
    return TranscriptArtifact.objects.create(
        source=source,
        job=source.jobs.filter(status=TranscriptionJob.Status.SUCCESS).order_by("-finished_at").first(),
        kind=kind,
        vault_file=vault_file,
        created_by=created_by if getattr(created_by, "is_authenticated", False) else None,
    )


def _hash_ip(ip: str) -> str:
    salt = getattr(settings, "SECRET_KEY", "")
    if not ip:
        return ""
    return hashlib.sha256(f"{salt}:{ip}".encode("utf-8")).hexdigest()


def record_event(*, request, source: TranscriptSource, event: str, seconds_played: int = 0, metadata: dict[str, Any] | None = None) -> TranscriptEvent:
    session_key = ""
    if hasattr(request, "session"):
        session_key = request.session.session_key or ""
        if not session_key:
            request.session.save()
            session_key = request.session.session_key or ""
    user = request.user if getattr(getattr(request, "user", None), "is_authenticated", False) else None
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR", "")
    event_obj = TranscriptEvent.objects.create(
        source=source,
        user=user,
        event=event,
        seconds_played=max(0, int(seconds_played or 0)),
        session_key=session_key,
        ip_hash=_hash_ip(ip),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:1000],
        referrer=request.META.get("HTTP_REFERER", "")[:1000],
        metadata=metadata or {},
    )
    if event in {TranscriptEvent.EventKind.IMPRESSION, TranscriptEvent.EventKind.PLAY}:
        TranscriptSource.objects.filter(pk=source.pk).update(views_count=F("views_count") + 1)
    return event_obj
