"""Forms for Bento — only the SQL **template** ModelForms.

Nodes and edges are edited as JSON (an ACE editor) rather than per-field forms,
so there are no dynamic property forms here; validation against a template's
``property_schema`` happens server-side in :mod:`toto.bento.graph_service`.
"""

from django import forms

from .models import BentoCategory, BentoEdgeType

FIELD_CLASS = (
    "w-full rounded-lg border px-3 py-2 text-sm outline-none "
    "shadow-inner transition focus:ring-2 focus:ring-current/20"
)

FIELD_THEME_CLASS = (
    "darkMode "
    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark placeholder:text-text-main-dark/45' "
    ": 'border-accent-2 bg-primary-bg-light text-text-main-light placeholder:text-text-main-light/45'"
)


def _style(field):
    field.widget.attrs.setdefault("class", FIELD_CLASS)
    field.widget.attrs.setdefault("x-bind:class", FIELD_THEME_CLASS)
    return field


class BentoCategoryForm(forms.ModelForm):
    class Meta:
        model = BentoCategory
        fields = ["name", "slug", "neo4j_label", "description", "property_schema", "internal"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Idea"}),
            "slug": forms.TextInput(attrs={"placeholder": "auto-filled from name (editable)"}),
            "neo4j_label": forms.TextInput(attrs={"placeholder": "auto-filled from name (editable)"}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "property_schema": forms.Textarea(attrs={"rows": 10}),
            "internal": forms.CheckboxInput(attrs={"class": "size-4 cursor-pointer"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name != "internal":  # don't apply text-input styling to the checkbox
                _style(field)


class BentoEdgeTypeForm(forms.ModelForm):
    class Meta:
        model = BentoEdgeType
        fields = [
            "name", "slug", "rel_type", "description", "allowed_sources",
            "allowed_targets", "property_schema", "directed",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Supports"}),
            "slug": forms.TextInput(attrs={"placeholder": "auto-filled from name (editable)"}),
            "rel_type": forms.TextInput(attrs={"placeholder": "auto-filled from name (editable)"}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "property_schema": forms.Textarea(attrs={"rows": 8}),
            "allowed_sources": forms.CheckboxSelectMultiple,
            "allowed_targets": forms.CheckboxSelectMultiple,
            "directed": forms.CheckboxInput(attrs={"class": "size-4 cursor-pointer"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Don't apply the text-input styling to the checkbox fields.
        skip = {"allowed_sources", "allowed_targets", "directed"}
        for name, field in self.fields.items():
            if name not in skip:
                _style(field)
