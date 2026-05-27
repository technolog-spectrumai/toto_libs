from django.contrib import admin

from .models import VodAccessGrant, VodCollection, VodPlaybackEvent, VodVideo


@admin.register(VodCollection)
class VodCollectionAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "access_mode", "required_plan", "bucket", "position")
    list_filter = ("access_mode", "required_plan")
    search_fields = ("title", "slug", "description")
    prepopulated_fields = {"slug": ("title",)}
    raw_id_fields = ("owner", "bucket", "cover_file", "required_plan")


@admin.register(VodVideo)
class VodVideoAdmin(admin.ModelAdmin):
    list_display = ("title", "collection", "status", "access_mode", "hls_ready", "required_plan", "position")
    list_filter = ("status", "access_mode", "hls_ready", "collection")
    search_fields = ("title", "slug", "description", "source_file__title")
    prepopulated_fields = {"slug": ("title",)}
    raw_id_fields = ("collection", "source_file", "poster_file", "required_plan", "hls_bucket")
    readonly_fields = ("hls_playlist_path", "hls_ready", "hls_built_at", "hls_error", "published_at")


@admin.register(VodAccessGrant)
class VodAccessGrantAdmin(admin.ModelAdmin):
    list_display = ("user", "collection", "video", "status", "invoice", "subscription", "starts_at", "ends_at")
    list_filter = ("status",)
    search_fields = ("user__username", "video__title", "collection__title", "note")
    raw_id_fields = ("user", "collection", "video", "subscription", "invoice")


@admin.register(VodPlaybackEvent)
class VodPlaybackEventAdmin(admin.ModelAdmin):
    list_display = ("video", "user", "event", "seconds_watched", "subscription_usage", "created_at")
    list_filter = ("event", "created_at")
    search_fields = ("video__title", "user__username", "session_key")
    raw_id_fields = ("video", "user", "subscription_usage")
    readonly_fields = ("ip_hash", "user_agent", "referrer", "created_at", "updated_at")
