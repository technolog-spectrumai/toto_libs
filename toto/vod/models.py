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

class TimestampedModel(models.Model):
    uid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        abstract = True


class VodAccessMode(models.TextChoices):
    PUBLIC = "public", _("Public")
    PRIVATE = "private", _("Private — readers list only")


# Access is collection-level; this alias is kept for import compatibility
VodVideoAccessMode = VodAccessMode


class VodCollection(TimestampedModel):
    """A reusable VOD library/category, e.g. Industrial Robotics footage."""

    title = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vod_collections",
    )
    bucket = models.ForeignKey(
        "vault.Bucket",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vod_collections",
        help_text=_("Vault bucket used for source media and generated HLS paths."),
    )
    cover_file = models.ForeignKey(
        "vault.VaultFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vod_collection_covers",
        help_text=_("Optional VaultFile image used as collection cover."),
    )
    access_mode = models.CharField(max_length=24, choices=VodAccessMode.choices, default=VodAccessMode.PUBLIC)
    readers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="vod_readable_collections",
        help_text=_("Users who can watch private content in this collection."),
    )
    writers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="vod_writable_collections",
        help_text=_("Users who can upload and manage videos in this collection."),
    )
    allow_downloads = models.BooleanField(default=False)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "title"]
        indexes = [
            models.Index(fields=["access_mode", "position"]),
            models.Index(fields=["slug"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)[:200]
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("vod:collection_detail", args=[self.slug])

    @property
    def is_publicly_listed(self) -> bool:
        return self.access_mode == VodAccessMode.PUBLIC

    @property
    def is_private(self) -> bool:
        return self.access_mode == VodAccessMode.PRIVATE

    def user_can_read(self, user) -> bool:
        if not user or not getattr(user, "is_authenticated", False):
            return self.access_mode == VodAccessMode.PUBLIC
        if getattr(user, "is_superuser", False):
            return True
        if self.owner_id == user.id:
            return True
        if self.access_mode == VodAccessMode.PUBLIC:
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


class VodVideo(TimestampedModel):
    """A VOD item backed by vault.VaultFile and optionally generated HLS output."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        PROCESSING = "processing", _("Processing")
        PUBLISHED = "published", _("Published")
        FAILED = "failed", _("Failed")
        ARCHIVED = "archived", _("Archived")

    collection = models.ForeignKey(VodCollection, on_delete=models.CASCADE, related_name="videos")
    source_file = models.ForeignKey(
        "vault.VaultFile",
        on_delete=models.PROTECT,
        related_name="vod_source_videos",
        help_text=_("Original unencrypted VaultFile video."),
    )
    poster_file = models.ForeignKey(
        "vault.VaultFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vod_posters",
        help_text=_("Optional VaultFile image poster."),
    )
    title = models.CharField(max_length=240)
    slug = models.SlugField(max_length=260)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    position = models.PositiveIntegerField(default=0)
    tags = models.JSONField(default=list, blank=True)

    hls_bucket = models.ForeignKey(
        "vault.Bucket",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vod_hls_outputs",
        help_text=_("Optional bucket override for generated HLS assets."),
    )
    hls_playlist_path = models.CharField(max_length=600, blank=True)
    hls_ready = models.BooleanField(default=False)
    hls_built_at = models.DateTimeField(null=True, blank=True)
    hls_error = models.TextField(blank=True)
    hls_segment_seconds = models.PositiveIntegerField(default=6)
    published_at = models.DateTimeField(null=True, blank=True)
    views_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["collection", "position", "title"]
        constraints = [
            models.UniqueConstraint(fields=["collection", "slug"], name="uniq_vod_video_slug_per_collection"),
        ]
        indexes = [
            models.Index(fields=["collection", "status"]),
            models.Index(fields=["status", "hls_ready"]),
            models.Index(fields=["slug"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)[:260]
        if self.status == self.Status.PUBLISHED and self.published_at is None:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.source_file_id:
            if getattr(self.source_file, "file_type", None) != "video":
                raise ValidationError({"source_file": _("Source VaultFile must have file_type='video'.")})
            if getattr(self.source_file, "is_encrypted", False):
                raise ValidationError({"source_file": _("VOD cannot use encrypted VaultFiles.")})

    @property
    def effective_access_mode(self) -> str:
        return self.collection.access_mode

    @property
    def effective_hls_bucket(self):
        return self.hls_bucket or self.collection.bucket or getattr(self.source_file, "bucket", None)

    @property
    def is_publicly_listed(self) -> bool:
        return self.status == self.Status.PUBLISHED and self.collection.access_mode == VodAccessMode.PUBLIC

    def hls_prefix(self) -> str:
        return f"vod/hls/{self.collection.slug}/{self.slug}"

    def playlist_url(self) -> str:
        if not self.hls_playlist_path:
            return ""
        return default_storage.url(self.hls_playlist_path)

    def poster_url(self) -> str:
        if self.poster_file_id and getattr(self.poster_file, "file", None):
            return self.poster_file.file.url
        return ""

    def source_url(self) -> str:
        if getattr(self.source_file, "file", None):
            return self.source_file.file.url
        return ""

    def get_absolute_url(self):
        return reverse("vod:video_detail", args=[self.collection.slug, self.slug])


class VodAccessGrant(TimestampedModel):
    """One-off access grant for a VOD collection or video."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        ACTIVE = "active", _("Active")
        REVOKED = "revoked", _("Revoked")
        EXPIRED = "expired", _("Expired")

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vod_access_grants")
    collection = models.ForeignKey(VodCollection, on_delete=models.CASCADE, null=True, blank=True, related_name="access_grants")
    video = models.ForeignKey(VodVideo, on_delete=models.CASCADE, null=True, blank=True, related_name="access_grants")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["collection", "status"]),
            models.Index(fields=["video", "status"]),
        ]

    def __str__(self):
        target = self.video or self.collection
        return f"{self.user} → {target} [{self.status}]"

    def clean(self):
        super().clean()
        if not self.collection_id and not self.video_id:
            raise ValidationError(_("Access grant must target either a collection or a video."))

    def is_current(self, now=None) -> bool:
        now = now or timezone.now()
        if self.status not in {self.Status.ACTIVE, self.Status.PENDING}:
            return False
        if self.starts_at and self.starts_at > now:
            return False
        if self.ends_at and self.ends_at <= now:
            return False
        return True


class VodPlaybackEvent(TimestampedModel):
    """Local VOD analytics, optionally linked to subscriptions.SubscriptionUsage."""

    class EventKind(models.TextChoices):
        IMPRESSION = "impression", _("Impression")
        PLAY = "play", _("Play")
        PROGRESS = "progress", _("Progress")
        COMPLETE = "complete", _("Complete")
        ERROR = "error", _("Error")

    video = models.ForeignKey(VodVideo, on_delete=models.CASCADE, related_name="playback_events")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    event = models.CharField(max_length=24, choices=EventKind.choices, default=EventKind.PLAY)
    session_key = models.CharField(max_length=80, blank=True)
    ip_hash = models.CharField(max_length=64, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    seconds_watched = models.PositiveIntegerField(default=0)
    referrer = models.URLField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["video", "event", "created_at"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["session_key"]),
        ]

    def __str__(self):
        return f"{self.video} / {self.event}"
