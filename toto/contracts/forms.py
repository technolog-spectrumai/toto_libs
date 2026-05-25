import yaml
from django import forms
from django.core.exceptions import ValidationError

from .models import Contract, ContractNode, ContractEdge, SUPPORTED_NODE_TYPES, SUPPORTED_EDGE_TYPES
from .widgets import AceYamlWidget


class ContractForm(forms.ModelForm):
    class Meta:
        model = Contract
        fields = ["name", "description", "code", "metadata"]
        widgets = {
            "code": AceYamlWidget(attrs={"rows": 30}),
            "description": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "code": (
                "YAML is an import/export snapshot of the contract graph. "
                "Platform services enforce real behavior."
            ),
        }


class ContractNodeForm(forms.ModelForm):
    class Meta:
        model = ContractNode
        fields = [
            "key", "node_type", "title", "description",
            "is_manual", "object_app", "object_model", "object_id",
            "metadata", "position_x", "position_y",
        ]

    def __init__(self, *args, **kwargs):
        self.contract = kwargs.pop("contract", None)
        super().__init__(*args, **kwargs)
        self.fields["node_type"].widget = forms.Select(
            choices=[("", "— choose —")] + [(t, t) for t in SUPPORTED_NODE_TYPES]
        )

    def clean(self):
        cleaned = super().clean()
        if self.contract:
            cleaned["contract"] = self.contract
        return cleaned


class ContractEdgeForm(forms.ModelForm):
    class Meta:
        model = ContractEdge
        fields = ["source", "target", "edge_type", "label", "description", "metadata"]

    def __init__(self, *args, **kwargs):
        self.contract = kwargs.pop("contract", None)
        super().__init__(*args, **kwargs)
        if self.contract:
            self.fields["source"].queryset = ContractNode.objects.filter(contract=self.contract)
            self.fields["target"].queryset = ContractNode.objects.filter(contract=self.contract)
        self.fields["edge_type"].widget = forms.Select(
            choices=[("", "— choose —")] + [(t, t) for t in SUPPORTED_EDGE_TYPES]
        )

    def clean(self):
        cleaned = super().clean()
        if self.contract:
            cleaned["contract"] = self.contract
        return cleaned
