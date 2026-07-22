from django import forms

from .models import DataConnector


class DataConnectorForm(forms.ModelForm):
    """Config editing. JSON fields are textareas; the model's ``clean()``
    validates mapping_spec/extract_config against the extractor + Bento."""

    class Meta:
        model = DataConnector
        fields = [
            "name",
            "description",
            "http_connector",
            "extractor_kind",
            "extract_config",
            "mapping_spec",
            "trusted",
            "is_active",
            "schedule_enabled",
            "interval_minutes",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "extract_config": forms.Textarea(attrs={"rows": 12, "spellcheck": "false"}),
            "mapping_spec": forms.Textarea(attrs={"rows": 16, "spellcheck": "false"}),
        }
