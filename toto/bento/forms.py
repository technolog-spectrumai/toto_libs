from django import forms
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError

from .models import (
    Category,
    IdeaBox,
    IdeaLink,
    SubjectReference,
    SUBJECT_REFERENCE_TARGETS,
    subject_reference_content_type_limit,
)

_TARGET_LABELS = {(app, model): label for app, model, label in SUBJECT_REFERENCE_TARGETS}

FIELD_CLASS = (
    "w-full rounded-lg border px-3 py-2 text-sm outline-none "
    "shadow-inner transition focus:ring-2 focus:ring-current/20"
)

FIELD_THEME_CLASS = (
    "darkMode "
    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark placeholder:text-text-main-dark/45' "
    ": 'border-accent-2 bg-primary-bg-light text-text-main-light placeholder:text-text-main-light/45'"
)

class IdeaBoxForm(forms.ModelForm):
    class Meta:
        model = IdeaBox
        fields = [
            "label",
            "category",
            "properties",
        ]
        labels = {
            "properties": "Metadata (JSON)",
        }
        widgets = {
            "label": forms.TextInput(attrs={"placeholder": "Short label, optional"}),
            "category": forms.Select(),
            "properties": forms.Textarea(attrs={"rows": 14, "placeholder": (
                '{\n'
                '  "body": "Main idea / description",\n'
                '  "source_title": "",\n'
                '  "source_url": "",\n'
                '  "source_type": "",\n'
                '  "quote": "",\n'
                '  "rating": 4\n'
                '}'
            )}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", FIELD_CLASS)
            field.widget.attrs.setdefault("x-bind:class", FIELD_THEME_CLASS)


class IdeaLinkForm(forms.ModelForm):
    class Meta:
        model = IdeaLink
        fields = ["from_box", "label", "to_box", "properties"]
        labels = {
            "properties": "Metadata (JSON)",
        }
        widgets = {
            "label": forms.TextInput(attrs={"placeholder": "about, supports, contradicts, expands..."}),
            "properties": forms.Textarea(attrs={"rows": 5, "placeholder": '{\n  "strength": 0.8\n}'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", FIELD_CLASS)
            field.widget.attrs.setdefault("x-bind:class", FIELD_THEME_CLASS)


class SubjectReferenceForm(forms.ModelForm):
    """Edge from a bento box out to one of the allowed external models.

    `content_type` is a `<select>` limited to the allowed targets; `object_id` is
    rendered manually in the template as an Alpine-populated dependent `<select>`
    (options fetched from `api_subject_options`). Existence is validated server-side.
    """

    class Meta:
        model = SubjectReference
        fields = ["box", "content_type", "object_id", "label", "properties"]
        labels = {
            "properties": "Metadata (JSON)",
        }
        widgets = {
            "content_type": forms.Select(attrs={"x-model": "contentTypeId", "@change": "onTypeChange()"}),
            "label": forms.TextInput(attrs={"placeholder": "references, about, located at..."}),
            "properties": forms.Textarea(attrs={"rows": 5, "placeholder": '{\n  "note": "why this is referenced"\n}'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["content_type"].queryset = (
            ContentType.objects.filter(subject_reference_content_type_limit())
            .order_by("app_label", "model")
        )
        self.fields["content_type"].label_from_instance = (
            lambda ct: _TARGET_LABELS.get((ct.app_label, ct.model), ct.name)
        )
        # object_id is rendered by the template (dependent select); keep the field
        # required but it doesn't need its own widget styling.
        for name, field in self.fields.items():
            if name == "object_id":
                continue
            field.widget.attrs.setdefault("class", FIELD_CLASS)
            field.widget.attrs.setdefault("x-bind:class", FIELD_THEME_CLASS)

    def clean(self):
        cleaned = super().clean()
        content_type = cleaned.get("content_type")
        object_id = cleaned.get("object_id")
        if content_type and object_id:
            model = content_type.model_class()
            exists = False
            if model is not None:
                try:
                    exists = model.objects.filter(pk=object_id).exists()
                except (ValueError, TypeError, ValidationError):
                    exists = False
            if not exists:
                self.add_error("object_id", "Selected subject does not exist.")
        return cleaned


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "slug", "description"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Category name"}),
            "slug": forms.TextInput(attrs={"placeholder": "unique-category-slug"}),
            "description": forms.Textarea(attrs={"rows": 5, "placeholder": "Optional description"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", FIELD_CLASS)
            field.widget.attrs.setdefault("x-bind:class", FIELD_THEME_CLASS)