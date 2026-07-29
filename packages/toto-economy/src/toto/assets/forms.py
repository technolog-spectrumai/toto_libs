from django import forms

from toto.assets.models import Agreement, Contract, LedgerAccount


_FIELD_CSS = (
    "w-full rounded-lg border px-3 py-2 text-sm outline-none transition "
    "focus:ring-2 focus:ring-current/20 border-accent-2 bg-primary-bg-light text-text-main-light"
)


class AgreementForm(forms.ModelForm):
    code = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 20, "id": "id_lapis_code"}),
        help_text="Lapis smart-contract YAML. Leave blank to link an existing contract via the dropdown.",
    )

    class Meta:
        model = Agreement
        fields = ["source_account", "target_account", "contract", "metadata"]
        widgets = {
            "metadata": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        active_accounts = LedgerAccount.objects.filter(active=True).order_by("code")
        self.fields["source_account"].queryset = active_accounts
        self.fields["target_account"].queryset = active_accounts
        self.fields["contract"].queryset = Contract.objects.all()
        self.fields["contract"].required = False
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {_FIELD_CSS}".strip()

    def clean_code(self):
        code = self.cleaned_data.get("code", "").strip()
        if code:
            from .lapis.loader import loads_contract
            from .lapis.compiler import ContractFlowValidator
            from .lapis.exceptions import LapisValidationError
            try:
                tree = loads_contract(code, fmt="yaml")
                ContractFlowValidator().validate(tree)
            except LapisValidationError as exc:
                raise forms.ValidationError(str(exc))
        return code

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get("source_account")
        target = cleaned.get("target_account")
        if source and target and source == target:
            raise forms.ValidationError("Source and target accounts must differ.")
        return cleaned


class ContractForm(forms.ModelForm):
    code = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 25, "id": "id_lapis_code"}),
        help_text="Contract Flow YAML (kind: contract_flow).",
    )

    class Meta:
        model = Contract
        fields = ["name", "code", "metadata"]
        widgets = {
            "metadata": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {_FIELD_CSS}".strip()

    def clean_code(self):
        code = self.cleaned_data.get("code", "").strip()
        if code:
            from .lapis.loader import loads_contract
            from .lapis.compiler import ContractFlowValidator
            from .lapis.exceptions import LapisValidationError
            try:
                tree = loads_contract(code, fmt="yaml")
                ContractFlowValidator().validate(tree)
            except LapisValidationError as exc:
                raise forms.ValidationError(str(exc))
        return code
