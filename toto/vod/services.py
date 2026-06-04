from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import uuid as _uuid
from dataclasses import dataclass
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify


from .models import (
    VodAccessGrant,
    VodAccessMode,
    # VodVideoAccessMode kept for ingress_vod backward compat
    VodCollection,
    VodPlaybackEvent,
    VodVideo,
    VodVideoAccessMode,
)


ACTIVE_GRANT_STATUSES = {VodAccessGrant.Status.ACTIVE, VodAccessGrant.Status.PENDING}


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str = ""
    subscription: Subscription | None = None
    grant: VodAccessGrant | None = None


def get_vault_models():
    try:
        return apps.get_model("vault", "Bucket"), apps.get_model("vault", "VaultFile")
    except LookupError as exc:
        raise RuntimeError("Install the existing vault app before toto.vod.") from exc


def user_is_vod_manager(user, collection: VodCollection | None = None) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    if collection is None:
        return False
    return collection.user_can_write(user)


def can_access_video(user, video: VodVideo) -> AccessDecision:
    if video.status != VodVideo.Status.PUBLISHED:
        if user_is_vod_manager(user, video.collection):
            return AccessDecision(True, "manager-preview")
        return AccessDecision(False, "not-published")

    if user_is_vod_manager(user, video.collection):
        return AccessDecision(True, "manager")

    if video.collection.user_can_read(user):
        return AccessDecision(True, video.effective_access_mode)
    if not user or not getattr(user, "is_authenticated", False):
        return AccessDecision(False, "login-required")
    return AccessDecision(False, "private")


def current_access_grant(user, video: VodVideo) -> VodAccessGrant | None:
    if not user or not getattr(user, "is_authenticated", False):
        return None
    now = timezone.now()
    qs = VodAccessGrant.objects.filter(
        user=user,
        status__in=ACTIVE_GRANT_STATUSES,
        starts_at__lte=now,
    ).filter(models_current_grant_filter(now))
    qs = qs.filter(models_video_or_collection_filter(video))
    for grant in qs.order_by("-created_at"):
        if grant.is_current(now=now):
            if grant.status == VodAccessGrant.Status.PENDING:
                grant.status = VodAccessGrant.Status.ACTIVE
                grant.save(update_fields=["status", "updated_at"])
            return grant
    return None


def models_current_grant_filter(now):
    from django.db.models import Q

    return Q(ends_at__isnull=True) | Q(ends_at__gt=now)


def models_video_or_collection_filter(video: VodVideo):
    from django.db.models import Q

    return Q(video=video) | Q(collection=video.collection, video__isnull=True)


def detect_upload_type(uploaded_file) -> str:
    """Use vault detection when available, but keep video/image fallback here."""

    _, VaultFile = get_vault_models()
    mime = (getattr(uploaded_file, "content_type", "") or "").lower()
    name = (getattr(uploaded_file, "name", "") or "").lower()
    detected = ""
    if hasattr(VaultFile, "detect_type"):
        detected = VaultFile.detect_type(mime)
    if detected in {"video", "image", "audio"}:
        return detected
    if mime.startswith("video/") or name.endswith((".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".ts", ".m2ts")):
        return "video"
    if mime.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg")):
        return "image"
    return detected or "text"


def get_or_create_vod_bucket(*, owner, name: str = "VOD"):
    Bucket, _ = get_vault_models()
    base_slug = slugify(f"{name}-{owner.pk if owner else 'system'}")[:120] or "vod"
    bucket, _ = Bucket.objects.get_or_create(
        slug=base_slug,
        defaults={"name": f"{name} {owner.pk if owner else 'system'}", "owner": owner},
    )
    return bucket


@transaction.atomic
def create_vault_file_from_upload(*, uploaded_file, owner, bucket=None, title: str = "", make_public: bool = True):
    _, VaultFile = get_vault_models()
    bucket = bucket or get_or_create_vod_bucket(owner=owner)
    file_type = detect_upload_type(uploaded_file)

    # Ensure the derived vault key won't collide within the bucket
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
def create_video_from_upload(
    *,
    collection: VodCollection,
    uploaded_file,
    owner,
    title: str,
    description: str = "",
    poster_file=None,
    status: str = VodVideo.Status.DRAFT,
    build_hls: bool = False,
):
    source = create_vault_file_from_upload(
        uploaded_file=uploaded_file,
        owner=owner,
        bucket=collection.bucket,
        title=title,
        make_public=True,
    )
    if getattr(source, "file_type", None) != "video":
        raise ValidationError("Uploaded file is not a recognized video.")

    video = VodVideo.objects.create(
        collection=collection,
        source_file=source,
        title=title,
        description=description,
        status=status,
    )
    if poster_file:
        poster = create_vault_file_from_upload(
            uploaded_file=poster_file,
            owner=owner,
            bucket=collection.bucket,
            title=f"{title} poster",
            make_public=True,
        )
        video.poster_file = poster
        video.save(update_fields=["poster_file", "updated_at"])
    if build_hls:
        build_hls_for_video(video, force=True)
    return video


def delete_storage_prefix(prefix: str):
    # Works for storages that support listdir/delete. S3Storage does; local storage does.
    try:
        dirs, files = default_storage.listdir(prefix)
    except Exception:
        return
    for filename in files:
        path = f"{prefix.rstrip('/')}/{filename}"
        try:
            default_storage.delete(path)
        except Exception:
            pass
    for dirname in dirs:
        delete_storage_prefix(f"{prefix.rstrip('/')}/{dirname}")


@transaction.atomic
def build_hls_for_video(video: VodVideo, *, force: bool = False, segment_seconds: int | None = None) -> VodVideo:
    video.full_clean()
    if video.hls_ready and video.hls_playlist_path and not force:
        return video

    segment_seconds = int(segment_seconds or video.hls_segment_seconds or 6)
    prefix = video.hls_prefix()
    if force:
        delete_storage_prefix(prefix)

    video.status = VodVideo.Status.PROCESSING
    video.hls_error = ""
    video.save(update_fields=["status", "hls_error", "updated_at"])

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            source_path = tmpdir / "source"
            with video.source_file.file.open("rb") as src, open(source_path, "wb") as dst:
                shutil.copyfileobj(src, dst)

            outdir = tmpdir / "hls"
            outdir.mkdir()
            playlist_path = outdir / "index.m3u8"
            segment_pattern = str(outdir / "segment_%05d.ts")
            cmd = [
                getattr(settings, "VOD_FFMPEG_BIN", "ffmpeg"),
                "-y",
                "-i",
                str(source_path),
                "-c:v",
                getattr(settings, "VOD_FFMPEG_VIDEO_CODEC", "libx264"),
                "-preset",
                getattr(settings, "VOD_FFMPEG_PRESET", "veryfast"),
                "-c:a",
                getattr(settings, "VOD_FFMPEG_AUDIO_CODEC", "aac"),
                "-b:a",
                getattr(settings, "VOD_FFMPEG_AUDIO_BITRATE", "128k"),
                "-hls_time",
                str(segment_seconds),
                "-hls_playlist_type",
                "vod",
                "-hls_segment_filename",
                segment_pattern,
                str(playlist_path),
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                raise RuntimeError(result.stderr[-4000:])

            for local_file in sorted(outdir.iterdir()):
                storage_path = f"{prefix}/{local_file.name}"
                if default_storage.exists(storage_path):
                    default_storage.delete(storage_path)
                with open(local_file, "rb") as f:
                    default_storage.save(storage_path, File(f))

        video.hls_playlist_path = f"{prefix}/index.m3u8"
        video.hls_ready = True
        video.hls_built_at = timezone.now()
        video.status = VodVideo.Status.PUBLISHED
        video.hls_error = ""
        video.save(update_fields=["hls_playlist_path", "hls_ready", "hls_built_at", "status", "hls_error", "updated_at"])
        return video
    except Exception as exc:
        video.status = VodVideo.Status.FAILED
        video.hls_ready = False
        video.hls_error = str(exc)
        video.save(update_fields=["status", "hls_ready", "hls_error", "updated_at"])
        raise


@transaction.atomic
def client_ip_hash(request) -> str:
    raw = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")
    if not raw:
        return ""
    secret = getattr(settings, "SECRET_KEY", "vod")
    return hashlib.sha256(f"{secret}:{raw}".encode("utf-8")).hexdigest()


def record_playback_event(*, request, video: VodVideo, event: str, seconds_watched: int = 0) -> VodPlaybackEvent:
    user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    session = getattr(request, "session", None)
    session_key = getattr(session, "session_key", "") or ""
    playback = VodPlaybackEvent.objects.create(
        video=video,
        user=user,
        event=event,
        session_key=session_key,
        ip_hash=client_ip_hash(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:512],
        seconds_watched=max(0, int(seconds_watched or 0)),
        referrer=request.META.get("HTTP_REFERER", "")[:200],
    )
    if event == VodPlaybackEvent.EventKind.PLAY:
        from django.db.models import F
        VodVideo.objects.filter(pk=video.pk).update(views_count=F("views_count") + 1)

    return playback
