from django import forms

from .models import (
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


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        css = "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20 border-accent-2 bg-primary-bg-light text-text-main-light"
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {css}".strip()


class FinancialInstrumentForm(StyledModelForm):
    class Meta:
        model = FinancialInstrument
        fields = ["reference", "instrument_type", "issuer", "status", "contract_account", "starts_at", "ends_at", "terms", "metadata"]
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "terms": forms.Textarea(attrs={"rows": 4}),
            "metadata": forms.Textarea(attrs={"rows": 4}),
        }


class EscrowContractForm(StyledModelForm):
    class Meta:
        model = EscrowContract
        fields = [
            "instrument", "buyer_account", "seller_account", "escrow_account", "asset", "amount_base_units",
            "status", "order_reference", "release_condition", "metadata",
        ]
        widgets = {
            "release_condition": forms.Textarea(attrs={"rows": 4}),
            "metadata": forms.Textarea(attrs={"rows": 4}),
        }


class ForwardContractForm(StyledModelForm):
    class Meta:
        model = ForwardContract
        fields = "__all__"
        widgets = {"settlement_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}


class FutureMarketForm(StyledModelForm):
    class Meta:
        model = FutureMarket
        fields = "__all__"


class FutureContractForm(StyledModelForm):
    class Meta:
        model = FutureContract
        fields = "__all__"
        widgets = {
            "settlement_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "opened_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "settled_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }


class RevenueShareContractForm(StyledModelForm):
    class Meta:
        model = RevenueShareContract
        fields = "__all__"
        widgets = {
            "active_from": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "active_until": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }


class RevenueShareRecipientForm(StyledModelForm):
    class Meta:
        model = RevenueShareRecipient
        fields = "__all__"


class OptionContractForm(StyledModelForm):
    class Meta:
        model = OptionContract
        fields = "__all__"
        widgets = {"expiry_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}


class VestingContractForm(StyledModelForm):
    class Meta:
        model = VestingContract
        fields = "__all__"
        widgets = {
            "start_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "cliff_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "end_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }


class StakingPositionForm(StyledModelForm):
    class Meta:
        model = StakingPosition
        fields = "__all__"
        widgets = {
            "locked_until": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "unstaked_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }
