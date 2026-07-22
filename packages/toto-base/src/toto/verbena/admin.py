from django.contrib import admin
from django import forms
from trix_editor.widgets import TrixEditorWidget


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
