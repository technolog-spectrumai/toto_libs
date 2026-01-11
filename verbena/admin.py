from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django import forms
from markdownx.widgets import MarkdownxWidget

from .models import Page, Section, Image, Tag


# ────────────────────────────────────────────────
# FORMS
# ────────────────────────────────────────────────

class SectionAdminForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = "__all__"
        widgets = {
            "content": MarkdownxWidget(),
        }


class SectionInlineForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = "__all__"
        widgets = {
            "content": MarkdownxWidget(),
        }


# ────────────────────────────────────────────────
# INLINES
# ────────────────────────────────────────────────

class SectionInline(admin.StackedInline):
    model = Section
    form = SectionInlineForm
    extra = 1
    fields = ["title", "content", "order"]
    ordering = ["order"]
    show_change_link = True


# ────────────────────────────────────────────────
# ADMINS
# ────────────────────────────────────────────────

@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ["title", "author", "created_at", "view_page"]
    search_fields = ["title", "description"]
    list_filter = ["author", "tags"]
    prepopulated_fields = {"slug": ("title",)}
    filter_horizontal = ["tags"]
    inlines = [SectionInline]

    def view_page(self, obj):
        url = reverse("verbena:page_detail", kwargs={"slug": obj.slug})
        return format_html('<a href="{}" target="_blank">🔗 View</a>', url)

    view_page.short_description = "Page URL"


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    form = SectionAdminForm
    list_display = ["title", "page", "order"]
    list_filter = ["page"]
    ordering = ["page", "order"]


@admin.register(Image)
class ImageAdmin(admin.ModelAdmin):
    list_display = ["title", "section", "order"]
    list_filter = ["section"]
    ordering = ["section", "order"]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}
