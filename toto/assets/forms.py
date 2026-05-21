from decimal import Decimal

from django import forms

from toto.assets.models import Asset, LedgerAccount, Tokenization, TokenizationDefaultReason


class TokenizationCreateForm(forms.Form):
    asset_name = forms.CharField(max_length=255, label="Asset name")
    unit_name = forms.CharField(max_length=20, label="Unit name")
    decimals = forms.IntegerField(min_value=0, max_value=19, initial=0)
    total_supply = forms.DecimalField(max_digits=24, decimal_places=8, min_value=Decimal("0.00000001"))
    reserve_account = forms.ModelChoiceField(
        queryset=LedgerAccount.objects.filter(active=True).order_by("code"),
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
        help_text="Describe the physical, legal, or custodial backing for this token.",
    )
    minting_authority = forms.CharField(max_length=255, required=False)
    metadata = forms.JSONField(required=False, initial=dict, widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, real_world_object=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.real_world_object = real_world_object

        from toto.people.models import Person

        self.fields["supervisor"].queryset = Person.objects.order_by("display_name")
        for field in self.fields.values():
            css_class = "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20 border-accent-2 bg-primary-bg-light text-text-main-light"
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {css_class}".strip()

    def clean_unit_name(self):
        unit_name = self.cleaned_data["unit_name"].strip().upper()
        if Asset.objects.filter(unit_name__iexact=unit_name).exists():
            raise forms.ValidationError("An asset with this unit name already exists.")
        return unit_name

    def clean(self):
        cleaned = super().clean()
        if self.real_world_object and Tokenization.objects.filter(real_world_object=self.real_world_object).exists():
            raise forms.ValidationError("This object is already tokenized and cannot be tokenized again.")
        return cleaned


class TokenizationDefaultForm(forms.Form):
    reason = forms.ChoiceField(choices=TokenizationDefaultReason.choices)
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Optional details for the default record.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css_class = "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20 border-accent-2 bg-primary-bg-light text-text-main-light"
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {css_class}".strip()
