from django.contrib import admin
from .models import Note, Tag


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "created_at", "is_public")
    search_fields = ("title", "category", "content")
    list_filter = ("category", "created_at", "is_public")
    filter_horizontal = ("tags",)

    fieldsets = (
        (None, {
            "fields": ("title", "content", "category", "metadata", "is_public")
        }),
        ("Relationships", {
            "fields": ("subject", "tags")
        }),
        ("Timestamps", {
            "fields": ("created_at",)
        }),
    )



@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)
    ordering = ("name",)
