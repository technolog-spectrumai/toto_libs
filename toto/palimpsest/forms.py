from django import forms
from trix_editor.widgets import TrixEditorWidget

from .models import Page, Section, Tag


FIELD_CLASS = (
    "w-full rounded-lg border px-3 py-2 text-sm outline-none "
    "shadow-inner transition focus:ring-2 focus:ring-current/20"
)

FIELD_THEME_CLASS = (
    "darkMode "
    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark placeholder:text-text-main-dark/45' "
    ": 'border-accent-2 bg-primary-bg-light text-text-main-light placeholder:text-text-main-light/45'"
)


class PageForm(forms.ModelForm):
    class Meta:
        model = Page
        fields = ["title", "slug", "description", "tags"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Essay title"}),
            "slug": forms.TextInput(attrs={"placeholder": "optional-custom-url"}),
            "description": forms.Textarea(attrs={
                "rows": 4,
                "placeholder": "A short deck for the piece. Think subtitle, not summary.",
            }),
            "tags": forms.SelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        self.fields["tags"].queryset = Tag.objects.order_by("name")
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", FIELD_CLASS)
            field.widget.attrs.setdefault("x-bind:class", FIELD_THEME_CLASS)


class SectionForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = ["title", "content", "author", "order", "tags"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Section heading, optional"}),
            "content": TrixEditorWidget(),
            "order": forms.NumberInput(attrs={"min": 0}),
            "tags": forms.SelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tags"].queryset = Tag.objects.order_by("name")
        for name, field in self.fields.items():
            if name == "content":
                continue
            field.widget.attrs.setdefault("class", FIELD_CLASS)
            field.widget.attrs.setdefault("x-bind:class", FIELD_THEME_CLASS)


class TagForm(forms.ModelForm):
    class Meta:
        model = Tag
        fields = ["name", "slug"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Tag name"}),
            "slug": forms.TextInput(attrs={"placeholder": "optional-tag-slug"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", FIELD_CLASS)
            field.widget.attrs.setdefault("x-bind:class", FIELD_THEME_CLASS)
