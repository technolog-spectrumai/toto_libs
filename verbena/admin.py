from django.contrib import admin
from django import forms
from markdownx.widgets import MarkdownxWidget

from .models import Page, Section, Image


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
    list_display = ["title", "author", "created_at"]
    search_fields = ["title", "description", "tags"]
    list_filter = ["author"]
    prepopulated_fields = {"slug": ("title",)}
    inlines = [SectionInline]


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
