from django.core.exceptions import ValidationError
import jsonschema
from .models import DataNode, CollectionType, Graph
from django import forms
from .merge import MergeStrategy


class FieldFactory:
    def __init__(self, field_def, initial=None):
        self.field_def = field_def
        self.initial = initial

    def create(self):
        ftype = self.field_def.get("type", "string")
        required = self.field_def.get("required", False)
        label = self.field_def.get("label", self.field_def["name"].capitalize())

        # Map JSON schema types → factory methods
        method = getattr(self, f"create_{ftype}", self.create_string)
        return method(label, required)

    # -------------------------
    # BASIC TYPES
    # -------------------------
    def create_string(self, label, required):
        return forms.CharField(label=label, required=required, initial=self.initial)

    def create_integer(self, label, required):
        return forms.IntegerField(label=label, required=required, initial=self.initial)

    def create_number(self, label, required):
        return forms.FloatField(label=label, required=required, initial=self.initial)

    def create_boolean(self, label, required):
        return forms.BooleanField(label=label, required=required, initial=self.initial)

    def create_text(self, label, required):
        return forms.CharField(
            label=label,
            required=required,
            initial=self.initial,
            widget=forms.Textarea
        )

    # -------------------------
    # DATE / TIME TYPES
    # -------------------------
    def create_date(self, label, required):
        return forms.DateField(
            label=label,
            required=required,
            initial=self.initial,
            widget=forms.DateInput(attrs={"type": "date"})
        )

    def create_datetime(self, label, required):
        return forms.DateTimeField(
            label=label,
            required=required,
            initial=self.initial,
            widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
        )

    def create_time(self, label, required):
        return forms.TimeField(
            label=label,
            required=required,
            initial=self.initial,
            widget=forms.TimeInput(attrs={"type": "time"})
        )

    # -------------------------
    # CHOICE FIELDS
    # -------------------------
    def create_choice(self, label, required):
        choices = self.field_def.get("choices", [])
        return forms.ChoiceField(
            label=label,
            required=required,
            initial=self.initial,
            choices=[(c, c) for c in choices],
        )

    # -------------------------
    # JSON / OBJECT
    # -------------------------
    def create_object(self, label, required):
        return forms.JSONField(
            label=label,
            required=required,
            initial=self.initial,
            widget=forms.Textarea
        )

    # -------------------------
    # ARRAY / LIST
    # -------------------------
    def create_array(self, label, required):
        return forms.CharField(
            label=label,
            required=required,
            initial=self.initial,
            help_text="Enter a JSON list, e.g. ['a', 'b', 'c']",
            widget=forms.Textarea
        )



class DynamicDataNodeForm(forms.ModelForm):
    """
    A form that renders only dynamic fields from CollectionType.form_layout
    (or a passed layout_json). It does not include name/collection_type.
    """

    class Meta:
        model = DataNode
        fields = ["name", "collection_type", "graph"]
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


class RunCollectorsForm(forms.Form):
    graph = forms.ModelChoiceField(
        queryset=Graph.objects.all(),
        required=True,
        help_text="Choose the graph to ingest data into."
    )
    #
    # models = forms.MultipleChoiceField(
    #     required=True,
    #     help_text="Select models to process.",
    #     widget=forms.CheckboxSelectMultiple
    # )
    #
    # def __init__(self, *args, **kwargs):
    #     super().__init__(*args, **kwargs)
    #
    #     # Dynamically load all models from allowed apps
    #     model_choices = []
    #     for app_label in apps.app_configs.keys():
    #         app = apps.get_app_config(app_label)
    #         for model in app.get_models():
    #             model_choices.append(
    #                 (f"{model._meta.app_label}.{model.__name__}", model.__name__)
    #             )
    #
    #     self.fields["models"].choices = model_choices


class MergeGraphsForm(forms.Form):
    new_graph_name = forms.CharField(label="Name of new merged graph")

    graph_b = forms.ModelChoiceField(
        queryset=Graph.objects.all(),
        label="Graph B (merge with selected Graph A)"
    )

    strategy = forms.ChoiceField(
        choices=[
            (MergeStrategy.INNER_JOIN, "Inner Join"),
            (MergeStrategy.LEFT_JOIN, "Left Join"),
            (MergeStrategy.RIGHT_JOIN, "Right Join"),
            (MergeStrategy.FULL_OUTER_JOIN, "Full Outer Join"),
            (MergeStrategy.UNION, "Union"),
            (MergeStrategy.INTERSECTION, "Intersection"),
            (MergeStrategy.A_MINUS_B, "A minus B"),
            (MergeStrategy.B_MINUS_A, "B minus A"),
        ],
        label="Merge Strategy",
    )
