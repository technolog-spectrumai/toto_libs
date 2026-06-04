from django import forms

from .models import (
    AmortizationContract,
    EscrowContract,
    FinancialInstrument,
    ForwardContract,
    FutureContract,
    FutureMarket,
    OptionContract,
    RevenueShareContract,
    RevenueShareRecipient,
    StakingPosition,
    VestingContract,
)

FIELD_CLASS = (
    "w-full rounded-lg border px-3 py-2 text-sm outline-none "
    "shadow-inner transition focus:ring-2 focus:ring-current/20"
)
FIELD_THEME = (
    "darkMode "
    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark placeholder:text-text-main-dark/45' "
    ": 'border-accent-2 bg-primary-bg-light text-text-main-light placeholder:text-text-main-light/45'"
)

_SPLIT_DT_WIDGET = lambda required=True: forms.SplitDateTimeWidget(  # noqa: E731
    date_attrs={"type": "date"},
    time_attrs={"type": "time"},
)


def _split_dt(required=True, label="", help_text=""):
    return forms.SplitDateTimeField(
        required=required,
        widget=forms.SplitDateTimeWidget(
            date_attrs={"type": "date"},
            time_attrs={"type": "time"},
        ),
        label=label,
        help_text=help_text,
    )


class _InstrumentNameMixin:
    """Validates that `name` is unique across FinancialInstrument.reference."""

    def clean_name(self):
        name = self.cleaned_data.get("name", "").strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        from .models import FinancialInstrument
        qs = FinancialInstrument.objects.filter(reference=name)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f"An instrument with reference \"{name}\" already exists.")
        return name


def _apply_bento_style(form):
    for field in form.fields.values():
        widget = field.widget
        if hasattr(widget, "widgets"):
            for subwidget in widget.widgets:
                subwidget.attrs.setdefault("class", FIELD_CLASS)
                subwidget.attrs.setdefault("x-bind:class", FIELD_THEME)
        else:
            widget.attrs.setdefault("class", FIELD_CLASS)
            widget.attrs.setdefault("x-bind:class", FIELD_THEME)


class FinancialInstrumentForm(forms.ModelForm):
    starts_at = _split_dt(required=False, label="Starts at")
    ends_at = _split_dt(required=False, label="Ends at")

    class Meta:
        model = FinancialInstrument
        fields = ["reference", "instrument_type", "issuer", "status", "contract_account", "starts_at", "ends_at", "terms", "metadata"]
        widgets = {
            "terms": forms.Textarea(attrs={"rows": 4}),
            "metadata": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


class EscrowContractForm(_InstrumentNameMixin, forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        label="Name",
        help_text="A unique name or reference for this escrow (e.g. 'escrow-deal-42').",
    )

    class Meta:
        model = EscrowContract
        fields = [
            "buyer_account", "seller_account", "escrow_account", "asset", "amount_base_units",
            "status", "order_reference", "release_condition", "metadata",
        ]
        widgets = {
            "release_condition": forms.Textarea(attrs={"rows": 4}),
            "metadata": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


class ForwardContractForm(_InstrumentNameMixin, forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        label="Name",
        help_text="A unique name for this forward contract.",
    )
    settlement_at = _split_dt(required=False, label="Settlement at")

    class Meta:
        model = ForwardContract
        fields = [
            "buyer_account", "seller_account", "underlying_asset", "quantity_base_units",
            "payment_asset", "payment_amount_base_units", "settlement_type", "settlement_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


class FutureMarketForm(forms.ModelForm):
    class Meta:
        model = FutureMarket
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


class FutureContractForm(_InstrumentNameMixin, forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        label="Name",
        help_text="A unique name for this futures contract.",
    )
    settlement_at = _split_dt(required=False, label="Settlement at")
    opened_at = _split_dt(required=False, label="Opened at")
    settled_at = _split_dt(required=False, label="Settled at")

    class Meta:
        model = FutureContract
        fields = [
            "market", "long_account", "short_account", "contract_count",
            "entry_price_base_units", "settlement_price_base_units",
            "settlement_at", "opened_at", "settled_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


class RevenueShareContractForm(_InstrumentNameMixin, forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        label="Name",
        help_text="A unique name for this revenue share contract.",
    )
    active_from = _split_dt(required=False, label="Active from")
    active_until = _split_dt(required=False, label="Active until")

    class Meta:
        model = RevenueShareContract
        fields = ["revenue_account", "revenue_asset", "active_from", "active_until", "metadata"]
        widgets = {
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


class RevenueShareRecipientForm(forms.ModelForm):
    class Meta:
        model = RevenueShareRecipient
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


class OptionContractForm(_InstrumentNameMixin, forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        label="Name",
        help_text="A unique name for this option contract.",
    )
    expiry_at = _split_dt(required=False, label="Expiry at")
    exercised_at = _split_dt(required=False, label="Exercised at")

    class Meta:
        model = OptionContract
        fields = [
            "option_type", "style", "buyer_account", "writer_account",
            "underlying_asset", "quantity_base_units", "payment_asset",
            "strike_price_base_units", "premium_base_units", "settlement_type",
            "expiry_at", "exercised_at", "metadata",
        ]
        widgets = {
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


class VestingContractForm(_InstrumentNameMixin, forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        label="Name",
        help_text="A unique name for this vesting contract.",
    )
    start_at = _split_dt(required=False, label="Start at")
    cliff_at = _split_dt(required=False, label="Cliff at")
    end_at = _split_dt(required=False, label="End at")

    class Meta:
        model = VestingContract
        fields = [
            "grantor_account", "beneficiary_account", "asset", "total_amount_base_units",
            "released_amount_base_units", "start_at", "cliff_at", "end_at", "release_frequency",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


class StakingPositionForm(_InstrumentNameMixin, forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        label="Name",
        help_text="A unique name for this staking position.",
    )
    locked_until = _split_dt(required=False, label="Locked until")
    unstaked_at = _split_dt(required=False, label="Unstaked at")

    class Meta:
        model = StakingPosition
        fields = [
            "staker_account", "staking_account", "staked_asset", "staked_amount_base_units",
            "reward_asset", "reward_rate_bps", "locked_until", "unstaked_at", "metadata",
        ]
        widgets = {
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


# ---------------------------------------------------------------------------
# Amortization forms
# ---------------------------------------------------------------------------

class AmortizationContractForm(_InstrumentNameMixin, forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        label="Name",
        help_text="A unique reference for this amortization contract.",
    )
    starts_at = _split_dt(required=False, label="Starts at")
    ends_at = _split_dt(required=False, label="Ends at")

    class Meta:
        model = AmortizationContract
        fields = [
            "source_account", "destination_account", "asset",
            "original_amount_base_units", "basis",
            "starts_at", "ends_at", "metadata",
        ]
        widgets = {
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


class RevenueShareRecipientInlineForm(forms.Form):
    """Add a single recipient to an existing RevenueShareContract."""
    account = forms.ModelChoiceField(
        queryset=None,
        label="Recipient account",
        help_text="Account that will receive a share of the revenue.",
    )
    share_percent = forms.DecimalField(
        min_value="0.01",
        max_value="100",
        decimal_places=2,
        label="Share (%)",
        help_text="Percentage of revenue (e.g. 25.00 for 25%).",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from toto.assets.models import LedgerAccount
        self.fields["account"].queryset = LedgerAccount.objects.all()
        _apply_bento_style(self)

    def get_share_bps(self):
        return int(self.cleaned_data["share_percent"] * 100)


class AmortizationEntryForm(forms.Form):
    """Form for recording a single amortization drawdown."""
    amount_base_units = forms.IntegerField(
        min_value=1,
        label="Amount (base units)",
        help_text="Amount to amortize in this step.",
    )
    source_type = forms.CharField(
        max_length=100,
        required=False,
        label="Source type",
        help_text="Optional type of the source object (e.g. 'invoice').",
    )
    source_id = forms.CharField(
        max_length=255,
        required=False,
        label="Source ID",
        help_text="Optional ID of the source object.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)
