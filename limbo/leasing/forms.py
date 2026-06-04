from django import forms

from .models import BillingPeriod, Lease

_CLS = (
    "w-full rounded-lg border px-3 py-2 text-sm outline-none "
    "shadow-inner transition focus:ring-2 focus:ring-current/20"
)
_THEME = (
    "darkMode "
    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark placeholder:text-text-main-dark/45' "
    ": 'border-accent-2 bg-primary-bg-light text-text-main-light placeholder:text-text-main-light/45'"
)


def _apply_style(form):
    for field in form.fields.values():
        w = field.widget
        if hasattr(w, "widgets"):
            for sw in w.widgets:
                sw.attrs.setdefault("class", _CLS)
                sw.attrs.setdefault("x-bind:class", _THEME)
        else:
            w.attrs.setdefault("class", _CLS)
            w.attrs.setdefault("x-bind:class", _THEME)


def _split_dt(required=True, label=""):
    return forms.SplitDateTimeField(
        required=required,
        widget=forms.SplitDateTimeWidget(
            date_attrs={"type": "date"},
            time_attrs={"type": "time"},
        ),
        label=label,
    )


class LeaseForm(forms.ModelForm):
    starts_at = _split_dt(label="Starts at")
    ends_at = _split_dt(required=False, label="Ends at")

    class Meta:
        model = Lease
        fields = [
            "leased_object", "lessor", "lessee",
            "lessor_account", "lessee_account", "payment_asset",
            "billing_period", "fixed_fee_base_units",
            "starts_at", "ends_at", "metadata",
        ]
        widgets = {
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_style(self)


class LeaseCreateForm(forms.Form):
    """Used by the contracts app when creating a contract-backed lease."""

    name = forms.CharField(max_length=255, label="Contract name")
    leased_object = forms.ModelChoiceField(queryset=None, label="Leased object")
    lessor = forms.ModelChoiceField(queryset=None, label="Lessor (person leasing out)")
    lessee = forms.ModelChoiceField(queryset=None, label="Lessee (person renting)")
    lessor_account = forms.ModelChoiceField(
        queryset=None, required=False, label="Lessor ledger account"
    )
    lessee_account = forms.ModelChoiceField(
        queryset=None, required=False, label="Lessee ledger account"
    )
    payment_asset = forms.ModelChoiceField(
        queryset=None, required=False, label="Payment currency / asset"
    )
    billing_period = forms.ChoiceField(choices=BillingPeriod.choices, label="Billing period")
    fixed_fee_base_units = forms.IntegerField(min_value=0, label="Fixed fee (base units)")
    starts_at = _split_dt(label="Starts at")
    ends_at = _split_dt(required=False, label="Ends at")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from toto.assets.models import Asset, LedgerAccount
        from toto.inventory.models import RealWorldObject
        from toto.people.models import Person

        self.fields["leased_object"].queryset = RealWorldObject.objects.order_by("name")
        self.fields["lessor"].queryset = Person.objects.order_by("display_name")
        self.fields["lessee"].queryset = Person.objects.order_by("display_name")
        self.fields["lessor_account"].queryset = LedgerAccount.objects.filter(active=True).order_by("code")
        self.fields["lessee_account"].queryset = LedgerAccount.objects.filter(active=True).order_by("code")
        self.fields["payment_asset"].queryset = Asset.objects.filter(active=True).order_by("unit_name")
        _apply_style(self)

    def clean(self):
        cleaned = super().clean()
        lessor = cleaned.get("lessor")
        lessee = cleaned.get("lessee")
        if lessor and lessee and lessor == lessee:
            raise forms.ValidationError("Lessor and lessee must be different people.")
        la = cleaned.get("lessor_account")
        lea = cleaned.get("lessee_account")
        if la and lea and la == lea:
            raise forms.ValidationError("Lessor and lessee accounts must differ.")
        return cleaned
