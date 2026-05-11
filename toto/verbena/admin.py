from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django import forms

from .models import (
    Page, Section, Subsection, Image, Tag,
    Book, Article, Audio, Video
)
from toto.vault.models import VaultFile  # Vault files


# ────────────────────────────────────────────────
# FORMS
# ────────────────────────────────────────────────

class SectionAdminForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = "__all__"
        widgets = {
            "content": forms.Textarea(attrs={"rows": 4}),
        }


class SectionInlineForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = "__all__"
        widgets = {
            "content": forms.Textarea(attrs={"rows": 4}),
        }


class SubsectionInlineForm(forms.ModelForm):
    class Meta:
        model = Subsection
        fields = "__all__"
        widgets = {
            "content": forms.Textarea(attrs={"rows": 4}),
        }


# ────────────────────────────────────────────────
# INLINES
# ────────────────────────────────────────────────

class SubsectionInline(admin.StackedInline):
    model = Subsection
    form = SubsectionInlineForm
    extra = 1
    fields = ["title", "content", "image", "order"]
    ordering = ["order"]
    show_change_link = True


class SectionInline(admin.StackedInline):
    model = Section
    form = SectionInlineForm
    extra = 1
    fields = ["title", "content", "author", "order"]
    ordering = ["order"]
    show_change_link = True


# ────────────────────────────────────────────────
# PAGE / SECTION / SUBSECTION / IMAGE / TAG
# ────────────────────────────────────────────────

@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ["title", "created_at", "author_list", "view_page"]
    search_fields = ["title", "description"]
    list_filter = ["tags"]
    prepopulated_fields = {"slug": ("title",)}
    filter_horizontal = ["tags"]
    inlines = [SectionInline]

    def author_list(self, obj):
        authors = obj.authors()
        return ", ".join(a.full_name for a in authors) if authors else "—"
    author_list.short_description = "Authors"

    def view_page(self, obj):
        url = reverse("verbena:page_detail", kwargs={"slug": obj.slug})
        return format_html('<a href="{}" target="_blank">🔗 View</a>', url)
    view_page.short_description = "Page URL"


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    form = SectionAdminForm
    list_display = ["title", "page", "order", "author"]
    list_filter = ["page", "author"]
    ordering = ["page", "order"]
    inlines = [SubsectionInline]
    filter_horizontal = ["tags"]  # topics removed


@admin.register(Subsection)
class SubsectionAdmin(admin.ModelAdmin):
    list_display = ["title", "section", "order", "image"]
    list_filter = ["section"]
    ordering = ["section", "order"]


@admin.register(Image)
class ImageAdmin(admin.ModelAdmin):
    list_display = ["title", "preview"]
    search_fields = ["title"]

    def preview(self, obj):
        if obj.file:
            return format_html(
                '<img src="{}" style="height:40px;border-radius:4px;" />',
                obj.file.url
            )
        return "—"
    preview.short_description = "Preview"


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}


# ────────────────────────────────────────────────
# LIBRARY ADMINS WITH VAULT FILE SUPPORT
# ────────────────────────────────────────────────

class ReferenceAdminForm(forms.ModelForm):
    class Meta:
        model = None  # Django sets this automatically
        fields = "__all__"
        widgets = {
            "abstract": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if hasattr(self.Meta, "model"):
            model_name = self.Meta.model.__name__.lower()
            compatible_types = {
                "book": ["pdf", "book"],
                "article": ["pdf", "article"],
                "audio": ["audio"],
                "video": ["video"],
            }
            if model_name in compatible_types:
                self.fields["vault_file"].queryset = VaultFile.objects.filter(
                    file_type__in=compatible_types[model_name]
                )


class ReferenceAdmin(admin.ModelAdmin):
    form = ReferenceAdminForm
    list_display = ["title", "author_list", "year", "bibtex_type", "vault_link"]
    search_fields = ["title", "abstract", "doi", "url"]
    list_filter = ["year", "tags"]
    filter_horizontal = ["authors", "tags"]

    def author_list(self, obj):
        authors = obj.authors.all()
        return ", ".join(a.full_name for a in authors) if authors else "—"
    author_list.short_description = "Authors"

    def vault_link(self, obj):
        if hasattr(obj, "vault_file") and obj.vault_file:
            url = obj.vault_file.get_public_url()
            if url:
                return format_html('<a href="{}" target="_blank">🔗 Vault File</a>', url)
        return "—"
    vault_link.short_description = "Vault File"


@admin.register(Book)
class BookAdmin(ReferenceAdmin):
    pass


@admin.register(Article)
class ArticleAdmin(ReferenceAdmin):
    pass


@admin.register(Audio)
class AudioAdmin(ReferenceAdmin):
    list_display = ["title", "artist", "album", "year", "bibtex_type", "file", "vault_link"]


@admin.register(Video)
class VideoAdmin(ReferenceAdmin):
    list_display = ["title", "director", "producer", "year", "bibtex_type", "file", "vault_link"]