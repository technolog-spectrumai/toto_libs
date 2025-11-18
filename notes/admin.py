from django.contrib import admin
from .models import Note, Tag


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "author", "created_at", "is_public")
    search_fields = ("title", "category", "content", "author__username")
    list_filter = ("category", "created_at", "is_public", "author")
    filter_horizontal = ("tags",)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)
    ordering = ("name",)
