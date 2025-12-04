# forms.py
from django import forms
from django.core.exceptions import ValidationError
import jsonschema

from .models import DataNode, CollectionType


class FieldFactory:
    def __init__(self, field_def, initial=None):
        self.field_def = field_def
        self.initial = initial

    def create(self):
        ftype = self.field_def.get("type", "string")
        required = self.field_def.get("required", False)
        label = self.field_def.get("label", self.field_def["name"].capitalize())
        method = getattr(self, f"create_{ftype}", self.create_string)
        return method(label, required)

    def create_string(self, label, required):
        return forms.CharField(label=label, required=required, initial=self.initial)

    def create_integer(self, label, required):
        return forms.IntegerField(label=label, required=required, initial=self.initial)

    def create_boolean(self, label, required):
        return forms.BooleanField(label=label, required=required, initial=self.initial)

    def create_text(self, label, required):
        return forms.CharField(
            label=label, required=required, initial=self.initial, widget=forms.Textarea
        )

    def create_date(self, label, required):
        return forms.DateField(label=label, required=required, initial=self.initial)

    def create_choice(self, label, required):
        choices = self.field_def.get("choices", [])
        return forms.ChoiceField(
            label=label,
            required=required,
            initial=self.initial,
            choices=[(c, c) for c in choices],
        )


class DynamicDataNodeForm(forms.ModelForm):
    """
    A form that renders only dynamic fields from CollectionType.form_layout
    (or a passed layout_json). It does not include name/collection_type.
    """

    class Meta:
        model = DataNode
        fields = ["name", "collection_type"]
        #exclude = ["data", "graph", "created_at"]

    def __init__(self, *args, layout_json=None, **kwargs):
        super().__init__(*args, **kwargs)

        # Determine collection type
        self.collection_type = None
        if self.instance and self.instance.collection_type:
            self.collection_type = self.instance.collection_type
        elif "collection_type" in self.initial:
            try:
                self.collection_type = CollectionType.objects.get(
                    pk=self.initial["collection_type"]
                )
            except CollectionType.DoesNotExist:
                pass

        # Use passed layout_json if provided, else fall back to collection_type.form_layout
        layout = layout_json or (self.collection_type.form_layout if self.collection_type else None)

        if layout:
            for field_def in layout.get("fields", []):
                fname = field_def["name"]
                initial = None
                if self.instance and self.instance.data:
                    initial = self.instance.data.get(fname)
                factory = FieldFactory(field_def, initial=initial)
                self.fields[fname] = factory.create()

    def clean(self):
        cleaned_data = super().clean()
        node_data = {}

        # collect values from dynamic fields only
        for fname in self.fields:
            value = cleaned_data.get(fname)
            if value is None and isinstance(self.fields[fname], forms.CharField):
                value = ""
            node_data[fname] = value

        # store dynamic data into DataNode.data
        cleaned_data["data"] = node_data

        # validate against schema if available
        if self.collection_type and self.collection_type.json_schema:
            try:
                jsonschema.validate(instance=node_data, schema=self.collection_type.json_schema)
            except jsonschema.ValidationError as e:
                raise ValidationError({"data": f"Schema validation error: {e.message}"})

        return cleaned_data
