from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from toto.subscriptions.models import TimestampedModel


def _format_ms(ms: int | None) -> str:
    ms = int(ms or 0)
    seconds, milli = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02}:{minutes:02}:{seconds:02}.{milli:03}"
    return f"{minutes:02}:{seconds:02}.{milli:03}"


class TranscriptAccessMode(models.TextChoices):
    PUBLIC = "public", _("Public")
    PRIVATE = "private", _("Private — readers list only")


class TranscriptCollection(TimestampedModel):
    """A reusable group of audio/video sources to transcribe."""

    title = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transcript_collections",
    )
    bucket = models.ForeignKey(
        "vault.Bucket",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transcript_collections",
        help_text=_("Vault bucket used for source media and generated transcript files."),
    )
    cover_file = models.ForeignKey(
        "vault.VaultFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transcript_collection_covers",
        help_text=_("Optional VaultFile image used as collection cover."),
    )
    access_mode = models.CharField(max_length=24, choices=TranscriptAccessMode.choices, default=TranscriptAccessMode.PRIVATE)
    readers = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="transcript_readable_collections")
    writers = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="transcript_writable_collections")
    allow_downloads = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "title"]
        indexes = [models.Index(fields=["access_mode", "position"]), models.Index(fields=["slug"])]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)[:200]
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("transcription:collection_detail", args=[self.slug])

    @property
    def is_private(self) -> bool:
        return self.access_mode == TranscriptAccessMode.PRIVATE

    def user_can_read(self, user) -> bool:
        if self.access_mode == TranscriptAccessMode.PUBLIC:
            return True
        if not user or not getattr(user, "is_authenticated", False):
            return False
        if getattr(user, "is_superuser", False):
            return True
        if self.owner_id == user.id:
            return True
        return self.readers.filter(pk=user.pk).exists() or self.writers.filter(pk=user.pk).exists()

    def user_can_write(self, user) -> bool:
        if not user or not getattr(user, "is_authenticated", False):
            return False
        if getattr(user, "is_superuser", False):
            return True
        if self.owner_id == user.id:
            return True
        return self.writers.filter(pk=user.pk).exists()


class TranscriptSource(TimestampedModel):
    """An audio/video input backed by vault.VaultFile."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        QUEUED = "queued", _("Queued")
        PROCESSING = "processing", _("Processing")
        TRANSCRIBED = "transcribed", _("Transcribed")
        FAILED = "failed", _("Failed")
        ARCHIVED = "archived", _("Archived")

    collection = models.ForeignKey(TranscriptCollection, on_delete=models.CASCADE, related_name="sources")
    source_file = models.ForeignKey(
        "vault.VaultFile",
        on_delete=models.PROTECT,
        related_name="transcript_source_files",
        help_text=_("Original unencrypted VaultFile audio or video."),
    )
    title = models.CharField(max_length=240)
    slug = models.SlugField(max_length=260)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    language = models.CharField(max_length=16, blank=True, help_text=_("Optional BCP-47 language code, e.g. en, pl."))
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    position = models.PositiveIntegerField(default=0)
    tags = models.JSONField(default=list, blank=True)
    transcript_text = models.TextField(blank=True)
    segments_count = models.PositiveIntegerField(default=0)
    transcribed_at = models.DateTimeField(null=True, blank=True)
    views_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["collection", "position", "title"]
        constraints = [models.UniqueConstraint(fields=["collection", "slug"], name="uniq_transcript_source_slug_per_collection")]
        indexes = [models.Index(fields=["collection", "status"]), models.Index(fields=["status", "transcribed_at"]), models.Index(fields=["slug"])]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)[:260]
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.source_file_id:
            file_type = getattr(self.source_file, "file_type", None)
            if file_type not in {"audio", "video"}:
                raise ValidationError({"source_file": _("Source VaultFile must have file_type='audio' or file_type='video'.")})
            if getattr(self.source_file, "is_encrypted", False):
                raise ValidationError({"source_file": _("Transcription cannot use encrypted VaultFiles unless your backend can decrypt them first.")})

    @property
    def effective_access_mode(self) -> str:
        return self.collection.access_mode

    @property
    def has_transcript(self) -> bool:
        return self.segments_count > 0 or bool(self.transcript_text)

    @property
    def is_video_source(self) -> bool:
        return getattr(self.source_file, "file_type", "") == "video"

    @property
    def is_audio_source(self) -> bool:
        return getattr(self.source_file, "file_type", "") == "audio"

    def source_url(self) -> str:
        if getattr(self.source_file, "file", None):
            return self.source_file.file.url
        return ""

    def get_absolute_url(self):
        return reverse("transcription:source_detail", args=[self.collection.slug, self.slug])

    def get_manage_url(self):
        return reverse("transcription:source_manage", args=[self.collection.slug, self.slug])


class TranscriptionJob(TimestampedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", _("Queued")
        RUNNING = "running", _("Running")
        SUCCESS = "success", _("Success")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")

    class Engine(models.TextChoices):
        DEFAULT = "default", _("Default backend")
        OPENAI_WHISPER = "openai_whisper", _("openai-whisper")
        FASTER_WHISPER = "faster_whisper", _("faster-whisper")
        COMMAND = "command", _("Command backend")
        CUSTOM = "custom", _("Custom callable")

    source = models.ForeignKey(TranscriptSource, on_delete=models.CASCADE, related_name="jobs")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.QUEUED)
    engine = models.CharField(max_length=40, choices=Engine.choices, default=Engine.DEFAULT)
    language = models.CharField(max_length=16, blank=True)
    detect_speakers = models.BooleanField(default=False)
    translate_to = models.CharField(max_length=16, blank=True)
    prompt = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    raw_response = models.JSONField(default=dict, blank=True)
    celery_task_id = models.CharField(max_length=255, blank=True, help_text=_("Celery task ID for async jobs — used for revocation."))
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["source", "status"]), models.Index(fields=["status", "created_at"])]

    def __str__(self):
        return f"{self.source} · {self.get_status_display()}"

    @property
    def duration_label(self) -> str:
        if not self.started_at or not self.finished_at:
            return ""
        return f"{int((self.finished_at - self.started_at).total_seconds())}s"


class TranscriptSpeaker(TimestampedModel):
    job = models.ForeignKey(TranscriptionJob, on_delete=models.CASCADE, related_name="speakers")
    label = models.CharField(max_length=80)
    person = models.ForeignKey("people.Person", on_delete=models.SET_NULL, null=True, blank=True, related_name="transcript_speaker_roles")

    class Meta:
        ordering = ["label"]
        constraints = [models.UniqueConstraint(fields=["job", "label"], name="uniq_transcript_speaker_label_per_job")]

    def __str__(self):
        return self.label


class TranscriptSegment(TimestampedModel):
    job = models.ForeignKey(TranscriptionJob, on_delete=models.CASCADE, related_name="segments")
    source = models.ForeignKey(TranscriptSource, on_delete=models.CASCADE, related_name="segments")
    speaker = models.ForeignKey(TranscriptSpeaker, on_delete=models.SET_NULL, null=True, blank=True, related_name="segments")
    index = models.PositiveIntegerField(default=0)
    start_ms = models.PositiveIntegerField(default=0)
    end_ms = models.PositiveIntegerField(default=0)
    text = models.TextField()
    confidence = models.FloatField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["source", "start_ms", "index"]
        indexes = [models.Index(fields=["source", "start_ms"]), models.Index(fields=["job", "index"])]

    def __str__(self):
        return f"{self.source} · {self.start_label}"

    @property
    def start_label(self) -> str:
        return _format_ms(self.start_ms)

    @property
    def end_label(self) -> str:
        return _format_ms(self.end_ms)


class TranscriptArtifact(TimestampedModel):
    class Kind(models.TextChoices):
        TXT = "txt", _("Plain text")
        JSON = "json", _("JSON")
        SRT = "srt", _("SRT subtitles")
        VTT = "vtt", _("WebVTT subtitles")
        SUMMARY = "summary", _("Summary")

    uid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    source = models.ForeignKey(TranscriptSource, on_delete=models.CASCADE, related_name="artifacts")
    job = models.ForeignKey(TranscriptionJob, on_delete=models.SET_NULL, null=True, blank=True, related_name="artifacts")
    kind = models.CharField(max_length=24, choices=Kind.choices)
    vault_file = models.ForeignKey("vault.VaultFile", on_delete=models.PROTECT, related_name="transcript_artifacts")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="transcript_artifacts")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["source", "kind"])]

    def __str__(self):
        return f"{self.source} · {self.kind}"


class TranscriptEvent(TimestampedModel):
    class EventKind(models.TextChoices):
        IMPRESSION = "impression", _("Impression")
        PLAY = "play", _("Play")
        TRANSCRIBE = "transcribe", _("Transcribe")
        EXPORT = "export", _("Export")
        DOWNLOAD = "download", _("Download")

    uid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    source = models.ForeignKey(TranscriptSource, on_delete=models.CASCADE, related_name="events")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="transcript_events")
    event = models.CharField(max_length=24, choices=EventKind.choices, default=EventKind.IMPRESSION)
    seconds_played = models.PositiveIntegerField(default=0)
    session_key = models.CharField(max_length=80, blank=True)
    ip_hash = models.CharField(max_length=64, blank=True)
    user_agent = models.TextField(blank=True)
    referrer = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["source", "event", "created_at"]), models.Index(fields=["event", "created_at"])]

    def __str__(self):
        return f"{self.source} · {self.event}"


# ---------------------------------------------------------------------------
# Whisper model weight registry
# ---------------------------------------------------------------------------

class WhisperModelConfig(TimestampedModel):
    """Tracks a locally cached Whisper model variant.

    Admin must create at least one record and set ``download_path`` before
    any download can be triggered through the UI.  The ``bucket`` FK is an
    optional organisational reference — it does **not** store files via Vault;
    the actual weights live at ``download_path`` on the server filesystem.
    """

    class Backend(models.TextChoices):
        FASTER_WHISPER = "faster_whisper", _("faster-whisper")
        OPENAI_WHISPER = "openai_whisper", _("openai-whisper")

    class Status(models.TextChoices):
        NOT_DOWNLOADED = "not_downloaded", _("Not downloaded")
        DOWNLOADING    = "downloading",    _("Downloading…")
        READY          = "ready",          _("Ready")
        FAILED         = "failed",         _("Failed")

    # Catalogue of available model sizes with approximate sizes in MB.
    CATALOGUE: list[tuple[str, str, int]] = [
        ("tiny",     "faster_whisper", 75),
        ("base",     "faster_whisper", 145),
        ("small",    "faster_whisper", 480),
        ("medium",   "faster_whisper", 1_500),
        ("large-v3", "faster_whisper", 3_100),
        ("tiny",     "openai_whisper", 75),
        ("base",     "openai_whisper", 145),
        ("small",    "openai_whisper", 480),
        ("medium",   "openai_whisper", 1_500),
        ("large",    "openai_whisper", 3_100),
    ]

    name         = models.CharField(max_length=80, help_text=_("Model size: tiny / base / small / medium / large-v3"))
    backend      = models.CharField(max_length=32, choices=Backend.choices, default=Backend.FASTER_WHISPER)
    download_path = models.CharField(
        max_length=512, blank=True,
        help_text=_("Absolute directory path on the server where this model will be stored. "
                    "Set this before triggering a download."),
    )
    bucket = models.ForeignKey(
        "vault.Bucket",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="whisper_model_configs",
        help_text=_("Optional vault bucket for organisational reference."),
    )
    status       = models.CharField(max_length=24, choices=Status.choices, default=Status.NOT_DOWNLOADED)
    is_active    = models.BooleanField(default=False, help_text=_("Use this model for default transcription jobs."))
    celery_task_id = models.CharField(max_length=255, blank=True)
    progress_pct = models.PositiveSmallIntegerField(default=0)
    error_message = models.TextField(blank=True)
    size_mb      = models.PositiveIntegerField(default=0, help_text=_("Approximate size in MB (populated automatically)."))

    class Meta:
        ordering = ["backend", "name"]
        constraints = [
            models.UniqueConstraint(fields=["name", "backend"], name="uniq_whisper_model_name_backend"),
        ]

    def __str__(self):
        return f"{self.get_backend_display()} / {self.name}"

    @property
    def display_name(self) -> str:
        return f"Whisper {self.name.capitalize()} ({self.get_backend_display()})"

    @property
    def is_ready(self) -> bool:
        return self.status == self.Status.READY

    @property
    def is_downloading(self) -> bool:
        return self.status == self.Status.DOWNLOADING

    def get_absolute_url(self):
        return reverse("transcription:model_setup")
