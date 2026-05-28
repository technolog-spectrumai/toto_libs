from django.contrib import admin

from .models import (
    SpeechModel,
    TranscriptArtifact,
    TranscriptCollection,
    TranscriptEvent,
    TranscriptionJob,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
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


@admin.register(SpeechModel)
class SpeechModelAdmin(admin.ModelAdmin):
    list_display = ("name", "backend", "is_active", "download_status", "download_progress", "device", "model_size")
    list_filter = ("backend", "is_active", "download_status", "device")
    search_fields = ("name", "slug", "description", "download_source")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("download_status", "download_progress", "download_error", "celery_task_id", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "slug", "backend", "description", "is_active")}),
        ("Inference", {"fields": ("model_size", "device", "compute_type", "beam_size", "language")}),
        ("Weights", {"fields": ("weights_file",)}),
        ("Download", {"fields": ("download_source", "download_status", "download_progress", "download_error", "celery_task_id")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(TranscriptEvent)
class TranscriptEventAdmin(admin.ModelAdmin):
    list_display = ("source", "user", "event", "seconds_played", "created_at")
    list_filter = ("event", "created_at")
    search_fields = ("source__title", "user__username", "session_key")
    raw_id_fields = ("source", "user")
    readonly_fields = ("ip_hash", "user_agent", "referrer", "created_at", "updated_at")
