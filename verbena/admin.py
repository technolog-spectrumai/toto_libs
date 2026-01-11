from django.contrib import admin
from .models import Page, Section, Image


class SectionInline(admin.StackedInline):
    model = Section
    extra = 1
    fields = ["title", "content", "order"]
    ordering = ["order"]
    show_change_link = True


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ["title", "author", "created_at"]
    search_fields = ["title", "description", "tags"]
    list_filter = ["author"]
    prepopulated_fields = {"slug": ("title",)}
    inlines = [SectionInline]


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ["title", "page", "order"]
    list_filter = ["page"]
    ordering = ["page", "order"]


@admin.register(Image)
class ImageAdmin(admin.ModelAdmin):
    list_display = ["title", "section", "order"]
    list_filter = ["section"]
    ordering = ["section", "order"]
