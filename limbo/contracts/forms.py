from django import forms

from toto.people.models import Person

from .models import Contract, ContractNode, ContractEdge, ContractSignatory, SUPPORTED_NODE_TYPES, SUPPORTED_EDGE_TYPES

PAYROLL_FREQUENCIES = [
    ("weekly", "Weekly"),
    ("biweekly", "Bi-weekly"),
    ("monthly", "Monthly"),
    ("quarterly", "Quarterly"),
    ("yearly", "Yearly"),
]

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


class PayrollCreateForm(forms.Form):
    name = forms.CharField(max_length=255)
    payer_account = forms.ModelChoiceField(queryset=None)
    worker_person = forms.ModelChoiceField(queryset=None)
    worker_account = forms.ModelChoiceField(queryset=None, label="Worker ledger account")
    asset = forms.ModelChoiceField(queryset=None)
    amount_base_units = forms.IntegerField(min_value=1, label="Budget (base units)")
    frequency = forms.ChoiceField(choices=PAYROLL_FREQUENCIES)
    source_app = forms.CharField(max_length=100, required=False, label="Source app (optional)")
    source_model = forms.CharField(max_length=100, required=False, label="Source model (optional)")
    source_id = forms.CharField(max_length=255, required=False, label="Source ID (optional)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from toto.assets.models import Asset, LedgerAccount
        self.fields["payer_account"].queryset = LedgerAccount.objects.filter(active=True).order_by("code")
        self.fields["worker_account"].queryset = LedgerAccount.objects.filter(active=True).order_by("code")
        self.fields["worker_person"].queryset = Person.objects.order_by("display_name")
        self.fields["asset"].queryset = Asset.objects.filter(active=True).order_by("unit_name")
        for fname in ("name", "amount_base_units", "source_app", "source_model", "source_id"):
            self.fields[fname].widget.attrs.update({"class": _CLS, ":class": _DARK})
        for fname in ("payer_account", "worker_person", "worker_account", "asset", "frequency"):
            self.fields[fname].widget.attrs.update({"class": _CLS, ":class": _DARK})


class PayrollDutyForm(forms.Form):
    amount_base_units = forms.IntegerField(min_value=1, label="Amount (base units)")
    due_at = forms.DateTimeField(
        required=False,
        label="Due at (leave blank for now)",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    source_app = forms.CharField(max_length=100, required=False, label="Source app (optional)")
    source_id = forms.CharField(max_length=255, required=False, label="Source ID (optional)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for fname in ("amount_base_units", "source_app", "source_id"):
            self.fields[fname].widget.attrs.update({"class": _CLS, ":class": _DARK})
        self.fields["due_at"].widget.attrs.update({"class": _CLS, ":class": _DARK})


INSURANCE_FREQUENCIES = [
    ("monthly", "Monthly"),
    ("quarterly", "Quarterly"),
    ("yearly", "Yearly"),
    ("biannual", "Bi-annual"),
    ("weekly", "Weekly"),
]


class LeaseCreateForm(forms.Form):
    """Contract-backed lease creation form (used from the contracts app)."""

    name = forms.CharField(max_length=255, label="Contract name")
    leased_object = forms.ModelChoiceField(queryset=None, label="Leased object")
    lessor = forms.ModelChoiceField(queryset=None, label="Lessor (person leasing out)")
    lessee = forms.ModelChoiceField(queryset=None, label="Lessee (person renting)")
    lessor_account = forms.ModelChoiceField(queryset=None, required=False, label="Lessor account")
    lessee_account = forms.ModelChoiceField(queryset=None, required=False, label="Lessee account")
    payment_asset = forms.ModelChoiceField(queryset=None, required=False, label="Payment currency")
    billing_period = forms.ChoiceField(
        choices=[
            ("once", "Once"), ("daily", "Daily"), ("weekly", "Weekly"),
            ("monthly", "Monthly"), ("yearly", "Yearly"), ("custom", "Custom"),
        ],
        label="Billing period",
    )
    fixed_fee_base_units = forms.IntegerField(min_value=0, label="Fixed fee (base units)")
    starts_at = forms.SplitDateTimeField(
        widget=forms.SplitDateTimeWidget(date_attrs={"type": "date"}, time_attrs={"type": "time"}),
        label="Starts at",
    )
    ends_at = forms.SplitDateTimeField(
        required=False,
        widget=forms.SplitDateTimeWidget(date_attrs={"type": "date"}, time_attrs={"type": "time"}),
        label="Ends at",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from toto.assets.models import Asset, LedgerAccount
        from toto.inventory.models import RealWorldObject

        self.fields["leased_object"].queryset = RealWorldObject.objects.order_by("name")
        self.fields["lessor"].queryset = Person.objects.order_by("display_name")
        self.fields["lessee"].queryset = Person.objects.order_by("display_name")
        self.fields["lessor_account"].queryset = LedgerAccount.objects.filter(active=True).order_by("code")
        self.fields["lessee_account"].queryset = LedgerAccount.objects.filter(active=True).order_by("code")
        self.fields["payment_asset"].queryset = Asset.objects.filter(active=True).order_by("unit_name")
        for fname in self.fields:
            w = self.fields[fname].widget
            if hasattr(w, "widgets"):
                for sw in w.widgets:
                    sw.attrs.setdefault("class", _CLS)
                    sw.attrs.setdefault(":class", _DARK)
            else:
                w.attrs.setdefault("class", _CLS)
                w.attrs.setdefault(":class", _DARK)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("lessor") and cleaned.get("lessee") and cleaned["lessor"] == cleaned["lessee"]:
            raise forms.ValidationError("Lessor and lessee must be different people.")
        return cleaned


class InsuranceCreateForm(forms.Form):
    name = forms.CharField(max_length=255)
    insured_person = forms.ModelChoiceField(queryset=None, label="Insured person")
    payer_account = forms.ModelChoiceField(queryset=None, label="Payer account (pays premiums)")
    insurer_account = forms.ModelChoiceField(queryset=None, label="Insurer account (receives premiums)")
    premium_asset = forms.ModelChoiceField(queryset=None, label="Premium currency")
    premium_amount_base_units = forms.IntegerField(min_value=1, label="Premium amount (base units)")
    premium_frequency = forms.ChoiceField(choices=INSURANCE_FREQUENCIES)
    insured_items = forms.ModelMultipleChoiceField(
        queryset=None,
        label="Inventory assets",
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text="Physical or real-world objects from inventory to cover.",
    )
    insured_financial_assets = forms.ModelMultipleChoiceField(
        queryset=None,
        label="Financial assets (ASA)",
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text="On-chain or financial assets to cover.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from toto.assets.models import Asset, LedgerAccount
        from toto.inventory.models import RealWorldObject
        self.fields["insured_person"].queryset = Person.objects.order_by("display_name")
        self.fields["payer_account"].queryset = LedgerAccount.objects.filter(active=True).order_by("code")
        self.fields["insurer_account"].queryset = LedgerAccount.objects.filter(active=True).order_by("code")
        self.fields["premium_asset"].queryset = Asset.objects.filter(active=True).order_by("unit_name")
        self.fields["insured_items"].queryset = RealWorldObject.objects.select_related("object_type").order_by("name")
        self.fields["insured_financial_assets"].queryset = Asset.objects.filter(active=True).order_by("unit_name")
        for fname in ("name", "premium_amount_base_units"):
            self.fields[fname].widget.attrs.update({"class": _CLS, ":class": _DARK})
        for fname in ("insured_person", "payer_account", "insurer_account", "premium_asset", "premium_frequency"):
            self.fields[fname].widget.attrs.update({"class": _CLS, ":class": _DARK})

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("insured_items") and not cleaned.get("insured_financial_assets"):
            raise forms.ValidationError("Select at least one inventory asset or financial asset to insure.")
        return cleaned


REPAYMENT_FREQUENCIES = [
    ("monthly", "Monthly"),
    ("quarterly", "Quarterly"),
    ("biannual", "Bi-annual"),
    ("yearly", "Yearly"),
    ("bullet", "Bullet (at maturity)"),
]


class LoanCreateForm(forms.Form):
    name = forms.CharField(max_length=255)
    lender_person = forms.ModelChoiceField(queryset=None, label="Lender")
    borrower_person = forms.ModelChoiceField(queryset=None, label="Borrower")
    lender_account = forms.ModelChoiceField(queryset=None, label="Lender account (disburses funds)")
    borrower_account = forms.ModelChoiceField(queryset=None, label="Borrower account (receives & repays)")
    loan_asset = forms.ModelChoiceField(queryset=None, label="Loan currency (same for both sides)")
    principal_base_units = forms.IntegerField(min_value=1, label="Principal (base units)")
    interest_rate_bps = forms.IntegerField(
        min_value=0, max_value=100000,
        label="Annual interest rate (basis points)",
        help_text="e.g. 500 = 5 %, 0 = interest-free",
    )
    repayment_frequency = forms.ChoiceField(choices=REPAYMENT_FREQUENCIES)
    term_months = forms.IntegerField(min_value=1, label="Term (months)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from toto.assets.models import Asset, LedgerAccount
        self.fields["lender_person"].queryset = Person.objects.order_by("display_name")
        self.fields["borrower_person"].queryset = Person.objects.order_by("display_name")
        self.fields["lender_account"].queryset = LedgerAccount.objects.filter(active=True).order_by("code")
        self.fields["borrower_account"].queryset = LedgerAccount.objects.filter(active=True).order_by("code")
        self.fields["loan_asset"].queryset = Asset.objects.filter(active=True).order_by("unit_name")
        for fname in ("name", "principal_base_units", "interest_rate_bps", "term_months"):
            self.fields[fname].widget.attrs.update({"class": _CLS, ":class": _DARK})
        for fname in ("lender_person", "borrower_person", "lender_account", "borrower_account",
                      "loan_asset", "repayment_frequency"):
            self.fields[fname].widget.attrs.update({"class": _CLS, ":class": _DARK})

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("lender_person") and cleaned.get("borrower_person"):
            if cleaned["lender_person"] == cleaned["borrower_person"]:
                raise forms.ValidationError("Lender and borrower must be different people.")
        return cleaned
