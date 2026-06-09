"""Forms for Bento.

Two kinds:

* ``BentoCategoryForm`` / ``BentoEdgeTypeForm`` — ordinary ModelForms for the SQL
  **templates**.
* ``build_node_form`` / ``build_edge_form`` — *dynamic* forms built at runtime
  from a template's ``property_schema`` (one typed field per declared property,
  plus a free-form ``extra`` JSON box). This mirrors manta's ``_build_form``
  pattern: classes are produced with ``type()`` from validated DB data.
"""

import json

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


# --------------------------------------------------------------------------
# template ModelForms
# --------------------------------------------------------------------------

class BentoCategoryForm(forms.ModelForm):
    class Meta:
        model = BentoCategory
        fields = ["name", "slug", "neo4j_label", "description", "property_schema", "color", "icon"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Idea"}),
            "slug": forms.TextInput(attrs={"placeholder": "auto from name if blank"}),
            "neo4j_label": forms.TextInput(attrs={"placeholder": "auto from name if blank"}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "property_schema": forms.Textarea(attrs={"rows": 10}),
            "icon": forms.TextInput(attrs={"placeholder": "fa-solid fa-lightbulb"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            _style(field)


class BentoEdgeTypeForm(forms.ModelForm):
    class Meta:
        model = BentoEdgeType
        fields = [
            "name", "slug", "rel_type", "description", "allowed_sources",
            "allowed_targets", "property_schema", "directed", "color",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Supports"}),
            "slug": forms.TextInput(attrs={"placeholder": "auto from name if blank"}),
            "rel_type": forms.TextInput(attrs={"placeholder": "auto from name if blank"}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "property_schema": forms.Textarea(attrs={"rows": 8}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            _style(field)


# --------------------------------------------------------------------------
# dynamic node/edge forms from a template's property_schema
# --------------------------------------------------------------------------

def _schema_field(spec):
    ftype = spec.get("type")
    required = bool(spec.get("required"))
    label = spec.get("label") or spec.get("name")
    help_text = spec.get("help") or ""
    common = {"required": required, "label": label, "help_text": help_text}

    if ftype == "text":
        return _style(forms.CharField(widget=forms.Textarea(attrs={"rows": 5}), **common))
    if ftype == "integer":
        return _style(forms.IntegerField(**common))
    if ftype == "float":
        return _style(forms.FloatField(**common))
    if ftype == "boolean":
        common["required"] = False
        return forms.BooleanField(**common)
    if ftype == "datetime":
        return _style(forms.DateTimeField(**common))
    if ftype == "json":
        return _style(forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), **common))
    # string / unknown
    return _style(forms.CharField(**common))


def _extra_field():
    return _style(forms.CharField(
        required=False,
        label="Extra (JSON)",
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Any extra properties as a JSON object.",
    ))


def build_node_form(category, data=None, initial=None):
    """Return an *instantiated* dynamic form for a category's node properties."""
    fields = {spec["name"]: _schema_field(spec) for spec in (category.property_schema or [])}
    fields["extra"] = _extra_field()
    form_cls = type("BentoNodeForm", (forms.Form,), fields)
    return form_cls(data=data, initial=initial)


def build_edge_form(edge_type, data=None, initial=None):
    """Dynamic form for an edge type's properties (source/target handled in view)."""
    fields = {spec["name"]: _schema_field(spec) for spec in (edge_type.property_schema or [])}
    fields["extra"] = _extra_field()
    form_cls = type("BentoEdgeForm", (forms.Form,), fields)
    return form_cls(data=data, initial=initial)


def collect_props(form):
    """Turn a dynamic node/edge form's cleaned_data into a props dict.

    The ``extra`` JSON box is parsed and merged in flat (declared keys win).
    """
    cleaned = dict(form.cleaned_data)
    extra_raw = cleaned.pop("extra", "") or ""
    props = {k: v for k, v in cleaned.items() if v not in (None, "")}
    if extra_raw.strip():
        try:
            extra = json.loads(extra_raw)
        except ValueError as exc:
            raise forms.ValidationError(f"Extra must be valid JSON: {exc}")
        if isinstance(extra, dict):
            for k, v in extra.items():
                props.setdefault(k, v)
    return props
