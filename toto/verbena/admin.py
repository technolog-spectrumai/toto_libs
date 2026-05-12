from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django import forms
from trix_editor.widgets import TrixEditorWidget

from .models import Page, Section, Tag, Reference


# ────────────────────────────────────────────────
# FORMS
# ────────────────────────────────────────────────

class SectionAdminForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = "__all__"
        widgets = {
            "content": TrixEditorWidget(),  # Use Trix for rich text
        }


class ReferenceAdminForm(forms.ModelForm):
    class Meta:
        model = Reference
        fields = "__all__"
        widgets = {
            "abstract": forms.Textarea(attrs={"rows": 4}),
        }


# ────────────────────────────────────────────────
# INLINES
# ────────────────────────────────────────────────

class SectionInline(admin.StackedInline):
    model = Section
    form = SectionAdminForm
    extra = 1
    fields = ["title", "content", "author", "order"]
    ordering = ["order"]
    show_change_link = True


# ────────────────────────────────────────────────
# PAGE ADMIN
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


# ────────────────────────────────────────────────
# SECTION ADMIN
# ────────────────────────────────────────────────

@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    form = SectionAdminForm
    list_display = ["title", "page", "order", "author"]
    list_filter = ["page", "author"]
    ordering = ["page", "order"]
    filter_horizontal = ["tags"]


# ────────────────────────────────────────────────
# TAG ADMIN
# ────────────────────────────────────────────────

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}


# ────────────────────────────────────────────────
# REFERENCE ADMIN
# ────────────────────────────────────────────────

@admin.register(Reference)
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
        if obj.vault_file:
            url = obj.vault_file.get_public_url()
            if url:
                return format_html('<a href="{}" target="_blank">🔗 Vault File</a>', url)
        return "—"
    vault_link.short_description = "Vault File"