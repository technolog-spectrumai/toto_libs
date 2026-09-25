from django.contrib import admin

from .models import Comment


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("pk", "author", "created_at", "edited_at", "deleted_at")
    list_filter = ("deleted_at",)
    search_fields = ("body",)
    readonly_fields = ("author", "created_at", "edited_at", "deleted_at", "reply_to")
