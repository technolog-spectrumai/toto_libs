from django import forms

from toto.people.models import Person

from .models import Contract, ContractNode, ContractEdge, ContractSignatory, SUPPORTED_NODE_TYPES, SUPPORTED_EDGE_TYPES

_CLS = "w-full rounded-lg border px-3 py-2 text-sm focus:outline-none focus:ring-1"
_DARK = "darkMode ? 'bg-bubble-bg-dark border-accent-1 text-text-main-dark' : 'bg-bubble-bg-light border-accent-2 text-text-main-light'"
_TA_CLS = "w-full rounded-lg border px-3 py-2 text-sm focus:outline-none focus:ring-1 resize-y"


def _attrs(extra=None):
    a = {"class": _CLS, ":class": _DARK}
    if extra:
        a.update(extra)
    return a


def _ta_attrs(rows=4):
    return {"class": _TA_CLS, ":class": _DARK, "rows": rows}


class ContractForm(forms.ModelForm):
    class Meta:
        model = Contract
        fields = ["name", "description", "body", "metadata"]
        widgets = {
            "description": forms.Textarea(attrs=_ta_attrs(4)),
            "body": forms.Textarea(attrs=_ta_attrs(10)),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update({"class": _CLS, ":class": _DARK})
        self.fields["metadata"].widget.attrs.update({"class": _TA_CLS, ":class": _DARK, "rows": 3})


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
            attrs=_attrs(),
            choices=[("", "— choose —")] + [(t, t) for t in SUPPORTED_NODE_TYPES],
        )
        for fname in ("key", "title", "object_app", "object_model", "object_id", "position_x", "position_y"):
            self.fields[fname].widget.attrs.update({"class": _CLS, ":class": _DARK})
        self.fields["description"].widget.attrs.update({"class": _TA_CLS, ":class": _DARK, "rows": 3})
        self.fields["metadata"].widget.attrs.update({"class": _TA_CLS, ":class": _DARK, "rows": 3})

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
        self.fields["source"].widget.attrs.update({"class": _CLS, ":class": _DARK})
        self.fields["target"].widget.attrs.update({"class": _CLS, ":class": _DARK})
        self.fields["edge_type"].widget = forms.Select(
            attrs=_attrs(),
            choices=[("", "— choose —")] + [(t, t) for t in SUPPORTED_EDGE_TYPES],
        )
        self.fields["label"].widget.attrs.update({"class": _CLS, ":class": _DARK})
        self.fields["description"].widget.attrs.update({"class": _TA_CLS, ":class": _DARK, "rows": 3})
        self.fields["metadata"].widget.attrs.update({"class": _TA_CLS, ":class": _DARK, "rows": 3})

    def clean(self):
        cleaned = super().clean()
        if self.contract:
            cleaned["contract"] = self.contract
        return cleaned


class ContractSignatoryForm(forms.ModelForm):
    class Meta:
        model = ContractSignatory
        fields = ["person", "is_required"]

    def __init__(self, *args, **kwargs):
        self.contract = kwargs.pop("contract", None)
        super().__init__(*args, **kwargs)
        if self.contract:
            already = ContractSignatory.objects.filter(contract=self.contract).values_list("person_id", flat=True)
            self.fields["person"].queryset = Person.objects.exclude(pk__in=already)
        self.fields["person"].widget.attrs.update({"class": _CLS, ":class": _DARK})
