from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django import forms
from trix_editor.widgets import TrixEditorWidget

from .models import Page, Section, Tag


# ────────────────────────────────────────────────
# REUSABLE BASE CLASSES (importable by other apps)
# ────────────────────────────────────────────────

def make_section_form(section_model):
    """Returns a ModelForm with TrixEditorWidget for the content field."""
    class _Form(forms.ModelForm):
        class Meta:
            model = section_model
            fields = "__all__"
            widgets = {"content": TrixEditorWidget()}
    return _Form


class SectionInlineMixin(admin.StackedInline):
    """
    Base inline for Section-like models. Subclasses must set `model`.
    The `form` is auto-built from `model` if not explicitly set.
    """
    extra = 1
    fields = ["title", "content", "author", "order"]
    ordering = ["order"]
    show_change_link = True

    def get_form_class(self):
        if not hasattr(self, "_auto_form"):
            self._auto_form = make_section_form(self.model)
        return self._auto_form

    def get_formset(self, request, obj=None, **kwargs):
        kwargs.setdefault("form", self.get_form_class())
        return super().get_formset(request, obj, **kwargs)


class PageAdminMixin(admin.ModelAdmin):
    """Base admin for Page-like models with slug auto-population."""
    prepopulated_fields = {"slug": ("title",)}
    search_fields = ["title", "description"]
    readonly_fields = ["created_at"]


# ────────────────────────────────────────────────
# VERBENA CONCRETE ADMIN
# ────────────────────────────────────────────────

class SectionInline(SectionInlineMixin):
    model = Section
    fields = ["title", "content", "author", "order"]


@admin.register(Page)
class PageAdmin(PageAdminMixin):
    list_display = ["title", "created_at", "author_list", "view_page"]
    list_filter = ["tags"]
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
    list_display = ["title", "page", "order", "author"]
    list_filter = ["page", "author"]
    ordering = ["page", "order"]
    filter_horizontal = ["tags"]

    def get_form(self, request, obj=None, **kwargs):
        kwargs.setdefault("form", make_section_form(Section))
        return super().get_form(request, obj, **kwargs)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}
