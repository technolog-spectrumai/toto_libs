from django import forms

from .models import (
    AmortizationContract,
    EscrowContract,
    FinancialInstrument,
    ForwardContract,
    FutureContract,
    FutureMarket,
    LeaseContract,
    OptionContract,
    RevenueShareContract,
    RevenueShareRecipient,
    StakingPosition,
    SubscriptionContract,
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
    class Meta:
        model = FinancialInstrument
        fields = ["reference", "instrument_type", "issuer", "status", "contract_account", "starts_at", "ends_at", "terms", "metadata"]
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "terms": forms.Textarea(attrs={"rows": 4}),
            "metadata": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


class EscrowContractForm(forms.ModelForm):
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


class ForwardContractForm(forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        label="Name",
        help_text="A unique name for this forward contract.",
    )

    class Meta:
        model = ForwardContract
        fields = [
            "buyer_account", "seller_account", "underlying_asset", "quantity_base_units",
            "payment_asset", "payment_amount_base_units", "settlement_type", "settlement_at",
        ]
        widgets = {"settlement_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}

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


class FutureContractForm(forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        label="Name",
        help_text="A unique name for this futures contract.",
    )

    class Meta:
        model = FutureContract
        fields = [
            "market", "long_account", "short_account", "contract_count",
            "entry_price_base_units", "settlement_price_base_units",
            "settlement_at", "opened_at", "settled_at",
        ]
        widgets = {
            "settlement_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "opened_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "settled_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


class RevenueShareContractForm(forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        label="Name",
        help_text="A unique name for this revenue share contract.",
    )

    class Meta:
        model = RevenueShareContract
        fields = ["revenue_account", "revenue_asset", "active_from", "active_until", "metadata"]
        widgets = {
            "active_from": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "active_until": forms.DateTimeInput(attrs={"type": "datetime-local"}),
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


class OptionContractForm(forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        label="Name",
        help_text="A unique name for this option contract.",
    )

    class Meta:
        model = OptionContract
        fields = [
            "option_type", "style", "buyer_account", "writer_account",
            "underlying_asset", "quantity_base_units", "payment_asset",
            "strike_price_base_units", "premium_base_units", "settlement_type",
            "expiry_at", "exercised_at", "metadata",
        ]
        widgets = {
            "expiry_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "exercised_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


class VestingContractForm(forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        label="Name",
        help_text="A unique name for this vesting contract.",
    )

    class Meta:
        model = VestingContract
        fields = [
            "grantor_account", "beneficiary_account", "asset", "total_amount_base_units",
            "released_amount_base_units", "start_at", "cliff_at", "end_at", "release_frequency",
        ]
        widgets = {
            "start_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "cliff_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "end_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


class StakingPositionForm(forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        label="Name",
        help_text="A unique name for this staking position.",
    )

    class Meta:
        model = StakingPosition
        fields = [
            "staker_account", "staking_account", "staked_asset", "staked_amount_base_units",
            "reward_asset", "reward_rate_bps", "locked_until", "unstaked_at", "metadata",
        ]
        widgets = {
            "locked_until": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "unstaked_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


# ---------------------------------------------------------------------------
# Lease forms
# ---------------------------------------------------------------------------

class LeaseContractForm(forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        label="Name",
        help_text="A unique name or reference for this lease (e.g. 'lease-office-2026').",
    )

    class Meta:
        model = LeaseContract
        fields = [
            "lessor_account", "lessee_account", "revenue_account",
            "leased_asset", "payment_asset",
            "billing_period", "fixed_fee_base_units",
            "starts_at", "ends_at", "metadata",
        ]
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


class SubscriptionContractForm(forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        label="Name",
        help_text="A unique name for this subscription (e.g. 'sub-acme-monthly').",
    )
    current_period_start = forms.SplitDateTimeField(
        widget=forms.SplitDateTimeWidget(
            date_attrs={"type": "date"},
            time_attrs={"type": "time"},
        ),
        label="Period start",
        help_text="When the first billing period begins (usually today).",
    )
    trial_ends_at = forms.SplitDateTimeField(
        required=False,
        widget=forms.SplitDateTimeWidget(
            date_attrs={"type": "date"},
            time_attrs={"type": "time"},
        ),
        label="Trial ends",
        help_text="Leave blank for no trial. First charge is deferred until this time.",
    )

    class Meta:
        model = SubscriptionContract
        fields = [
            "subscriber_account", "provider_account", "asset",
            "amount_base_units", "billing_cycle", "trial_ends_at",
            "current_period_start", "metadata",
        ]
        widgets = {"metadata": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


# ---------------------------------------------------------------------------
# Amortization forms
# ---------------------------------------------------------------------------

class AmortizationContractForm(forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        label="Name",
        help_text="A unique reference for this amortization contract.",
    )

    class Meta:
        model = AmortizationContract
        fields = [
            "source_account", "destination_account", "asset",
            "original_amount_base_units", "basis",
            "starts_at", "ends_at", "metadata",
        ]
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bento_style(self)


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
