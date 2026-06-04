from django import forms
from trix_editor.widgets import TrixEditorWidget

from toto.verbena.forms import apply_oya_field_styles

from .models import Page, Section, Tag


class PageForm(forms.ModelForm):
    class Meta:
        model = Page
        fields = ["title", "slug", "description", "tags", "is_private"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Essay title"}),
            "slug": forms.TextInput(attrs={"placeholder": "optional-custom-url"}),
            "description": forms.Textarea(attrs={
                "rows": 4,
                "placeholder": "A short deck for the piece. Think subtitle, not summary.",
            }),
            "tags": forms.SelectMultiple(),
            "is_private": forms.CheckboxInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        self.fields["tags"].queryset = Tag.objects.order_by("name")
        apply_oya_field_styles(self.fields, skip={"is_private"})


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
        apply_oya_field_styles(self.fields, skip={"content"})


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
        apply_oya_field_styles(self.fields)
