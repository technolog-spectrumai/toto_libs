from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from toto.treasury.models import (
    Budget, BudgetItemStatus, BudgetLedgerAccount, BudgetItem,
    BudgetStreamType, BudgetStatus, StreamDirection,
)


_WIDGET_CLS = "w-full rounded-lg border px-3 py-2 text-sm"


class BudgetForm(forms.ModelForm):
    class Meta:
        model = Budget
        fields = [
            "code", "name", "description", "status", "asset",
            "budget_account", "owner_type", "owner_id",
            "starts_at", "ends_at", "metadata",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "metadata": forms.Textarea(attrs={"rows": 3}),
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from toto.assets.models import LedgerAccount, Asset
        self.fields["asset"].queryset = Asset.objects.filter(active=True).order_by("unit_name")
        self.fields["budget_account"].queryset = LedgerAccount.objects.filter(active=True).order_by("code")


class BudgetLedgerAccountForm(forms.ModelForm):
    class Meta:
        model = BudgetLedgerAccount
        fields = [
            "ledger_account", "role", "is_default",
            "can_receive", "can_pay", "can_commit",
            "is_active", "metadata",
        ]
        widgets = {
            "metadata": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, budget=None, **kwargs):
        super().__init__(*args, **kwargs)
        from toto.assets.models import LedgerAccount
        self.budget = budget
        self.fields["ledger_account"].queryset = LedgerAccount.objects.filter(active=True).order_by("code")


class BudgetStreamTypeForm(forms.ModelForm):
    class Meta:
        model = BudgetStreamType
        fields = [
            "code", "name", "direction", "namespace", "description",
            "is_system", "is_active", "sort_order", "metadata",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "metadata": forms.Textarea(attrs={"rows": 2}),
        }


class BudgetItemForm(forms.ModelForm):
    class Meta:
        model = BudgetItem
        fields = [
            "title", "description", "stream_type", "account_binding",
            "status", "asset", "amount_base_units",
            "ledger_transaction", "obligation", "allocation", "contract",
            "counterparty", "source_type", "source_id", "source_label",
            "external_reference", "due_at", "metadata",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "metadata": forms.Textarea(attrs={"rows": 2}),
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }
        labels = {
            "description": _("Description (explain what this item is and why it exists)"),
        }

    def __init__(self, *args, budget=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.budget = budget
        if budget:
            self.fields["asset"].initial = budget.asset
            self.fields["asset"].queryset = type(budget.asset).objects.filter(pk=budget.asset_id)
            self.fields["account_binding"].queryset = BudgetLedgerAccount.objects.filter(
                budget=budget, is_active=True
            ).select_related("ledger_account")
            self.fields["stream_type"].queryset = BudgetStreamType.objects.filter(is_active=True).order_by("sort_order", "code")
