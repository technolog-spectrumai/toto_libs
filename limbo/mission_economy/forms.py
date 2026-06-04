from decimal import Decimal

from django import forms

from toto.mission_economy.models import ProjectTokenization, ProjectTokenizationDefaultReason

_CSS_BASE = (
    "w-full rounded-lg border px-3 py-2 text-sm outline-none transition "
    "focus:ring-2 focus:ring-current/20"
)
_CSS_COLORS = (
    "darkMode ? 'border-accent-1 bg-bubble-bg-dark text-text-main-dark'"
    " : 'border-accent-2 bg-bubble-bg-light text-text-main-light'"
)


class ProjectTokenizationCreateForm(forms.Form):
    asset_name = forms.CharField(max_length=255, label="Asset name")
    unit_name = forms.CharField(max_length=20, label="Unit name")
    decimals = forms.IntegerField(min_value=0, max_value=19, initial=0)
    total_supply = forms.DecimalField(max_digits=24, decimal_places=8, min_value=Decimal("0.00000001"))
    reserve_account = forms.ModelChoiceField(
        queryset=None,
        help_text="Initial supply is issued to this reserve account.",
    )
    supervisor = forms.ModelChoiceField(
        queryset=None,
        required=False,
        help_text="Person responsible for supervising this tokenization.",
    )
    is_currency = forms.BooleanField(required=False, label="Accepted as payment currency")
    backing_document = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Describe the legal or financial backing for this project token.",
    )
    minting_authority = forms.CharField(max_length=255, required=False)
    metadata = forms.JSONField(required=False, initial=dict, widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project

        from toto.assets.models import LedgerAccount
        from toto.people.models import Person

        self.fields["reserve_account"].queryset = LedgerAccount.objects.filter(active=True).order_by("code")
        self.fields["supervisor"].queryset = Person.objects.order_by("display_name")

        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "h-4 w-4 rounded")
            else:
                existing = field.widget.attrs.get("class", "")
                field.widget.attrs["class"] = f"{existing} {_CSS_BASE}".strip()
                field.widget.attrs[":class"] = _CSS_COLORS

    def clean_unit_name(self):
        from toto.assets.models import Asset
        unit_name = self.cleaned_data["unit_name"].strip().upper()
        if Asset.objects.filter(unit_name__iexact=unit_name).exists():
            raise forms.ValidationError("An asset with this unit name already exists.")
        return unit_name

    def clean(self):
        cleaned = super().clean()
        if self.project and ProjectTokenization.objects.filter(project=self.project).exists():
            raise forms.ValidationError("This project is already tokenized and cannot be tokenized again.")
        return cleaned


class ProjectTokenizationDefaultForm(forms.Form):
    reason = forms.ChoiceField(choices=ProjectTokenizationDefaultReason.choices)
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Optional details for the default record.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {_CSS_BASE}".strip()
            field.widget.attrs[":class"] = _CSS_COLORS
