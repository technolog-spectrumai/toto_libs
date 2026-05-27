from django.contrib import admin

from .models import (
    TranscriptArtifact,
    TranscriptCollection,
    TranscriptEvent,
    TranscriptionJob,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
    WhisperModelConfig,
)


class TranscriptSegmentInline(admin.TabularInline):
    model = TranscriptSegment
    extra = 0
    readonly_fields = ("index", "start_ms", "end_ms", "speaker", "text", "confidence")
    fields = readonly_fields


@admin.register(TranscriptCollection)
class TranscriptCollectionAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "access_mode", "bucket", "position")
    list_filter = ("access_mode",)
    search_fields = ("title", "slug", "description")
    prepopulated_fields = {"slug": ("title",)}
    raw_id_fields = ("owner", "bucket", "cover_file")
    filter_horizontal = ("readers", "writers")


@admin.register(TranscriptSource)
class TranscriptSourceAdmin(admin.ModelAdmin):
    list_display = ("title", "collection", "status", "language", "segments_count", "views_count", "position")
    list_filter = ("status", "language", "collection")
    search_fields = ("title", "slug", "description", "transcript_text", "source_file__title")
    prepopulated_fields = {"slug": ("title",)}
    raw_id_fields = ("collection", "source_file")
    readonly_fields = ("transcript_text", "segments_count", "transcribed_at", "views_count")


@admin.register(TranscriptionJob)
class TranscriptionJobAdmin(admin.ModelAdmin):
    list_display = ("source", "status", "engine", "language", "started_at", "finished_at")
    list_filter = ("status", "engine", "language")
    search_fields = ("source__title", "error_message")
    raw_id_fields = ("source",)
    readonly_fields = ("error_message", "raw_response", "started_at", "finished_at")
    inlines = [TranscriptSegmentInline]


@admin.register(TranscriptSpeaker)
class TranscriptSpeakerAdmin(admin.ModelAdmin):
    list_display = ("label", "job", "person")
    search_fields = ("label", "person__name", "job__source__title")
    raw_id_fields = ("job", "person")


@admin.register(TranscriptArtifact)
class TranscriptArtifactAdmin(admin.ModelAdmin):
    list_display = ("source", "kind", "vault_file", "created_by", "created_at")
    list_filter = ("kind", "created_at")
    search_fields = ("source__title", "vault_file__title")
    raw_id_fields = ("source", "job", "vault_file", "created_by")


@admin.register(WhisperModelConfig)
class WhisperModelConfigAdmin(admin.ModelAdmin):
    list_display = ("__str__", "backend", "status", "is_active", "progress_pct", "download_path", "bucket")
    list_filter = ("backend", "status", "is_active")
    search_fields = ("name", "download_path")
    readonly_fields = ("status", "progress_pct", "celery_task_id", "error_message", "created_at", "updated_at")
    raw_id_fields = ("bucket",)
    fieldsets = (
        (None, {"fields": ("name", "backend", "bucket")}),
        ("Storage", {"fields": ("download_path", "size_mb"), "description": "Set download_path to an absolute server directory before triggering a download from the UI."}),
        ("Status (read-only)", {"fields": ("status", "progress_pct", "is_active", "celery_task_id", "error_message")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(TranscriptEvent)
class TranscriptEventAdmin(admin.ModelAdmin):
    list_display = ("source", "user", "event", "seconds_played", "created_at")
    list_filter = ("event", "created_at")
    search_fields = ("source__title", "user__username", "session_key")
    raw_id_fields = ("source", "user")
    readonly_fields = ("ip_hash", "user_agent", "referrer", "created_at", "updated_at")
