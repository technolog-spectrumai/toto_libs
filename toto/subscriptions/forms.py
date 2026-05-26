from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import forms
from django.utils.translation import gettext_lazy as _

from toto.assets.models import Asset, LedgerAccount, to_base_units

from .models import (
    BillingInterval,
    Subscription,
    SubscriptionCustomer,
    SubscriptionFeature,
    SubscriptionInvoice,
    SubscriptionPlan,
    SubscriptionPrice,
)
from .services import record_usage


class SubscriptionPlanForm(forms.ModelForm):
    class Meta:
        model = SubscriptionPlan
        fields = ["community", "name", "slug", "code", "kind", "status", "description", "is_public", "trial_days", "max_subscribers", "owner", "metadata"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3}), "metadata": forms.Textarea(attrs={"rows": 3})}


class SubscriptionPriceForm(forms.ModelForm):
    amount_display = forms.DecimalField(max_digits=30, decimal_places=18, min_value=Decimal("0"), label=_("Amount"))
    setup_fee_display = forms.DecimalField(max_digits=30, decimal_places=18, min_value=Decimal("0"), required=False, label=_("Setup fee"))

    class Meta:
        model = SubscriptionPrice
        fields = ["name", "code", "asset", "amount_display", "interval", "interval_count", "setup_fee_display", "payer_must_prepay", "receiving_account", "is_active", "metadata"]
        widgets = {"metadata": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, plan=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.plan = plan
        self.fields["asset"].queryset = Asset.objects.filter(active=True)
        self.fields["receiving_account"].queryset = LedgerAccount.objects.filter(active=True)
        if self.instance.pk and self.instance.asset_id:
            asset = self.instance.asset
            self.initial.setdefault("amount_display", Decimal(str(self.instance.amount_base_units)) / (Decimal(10) ** asset.decimals))
            self.initial.setdefault("setup_fee_display", Decimal(str(self.instance.setup_fee_base_units)) / (Decimal(10) ** asset.decimals))

    def clean(self):
        cleaned = super().clean()
        asset = cleaned.get("asset")
        amount = cleaned.get("amount_display") or Decimal("0")
        setup = cleaned.get("setup_fee_display") or Decimal("0")
        if asset:
            try:
                cleaned["amount_base_units"] = to_base_units(amount, asset.decimals)
                cleaned["setup_fee_base_units"] = to_base_units(setup, asset.decimals)
            except (InvalidOperation, Exception) as exc:
                raise forms.ValidationError(str(exc)) from exc
        if cleaned.get("interval") == BillingInterval.ONCE and cleaned.get("interval_count") != 1:
            self.add_error("interval_count", _("One-off prices must use interval_count=1."))
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.amount_base_units = self.cleaned_data["amount_base_units"]
        instance.setup_fee_base_units = self.cleaned_data["setup_fee_base_units"]
        if self.plan and not instance.plan_id:
            instance.plan = self.plan
        if commit:
            instance.save()
        return instance


class SubscriptionFeatureForm(forms.ModelForm):
    class Meta:
        model = SubscriptionFeature
        fields = ["code", "name", "kind", "quantity", "unit", "reset_interval", "hard_limit", "active", "metadata"]
        widgets = {"metadata": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, plan=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.plan = plan

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.plan and not instance.plan_id:
            instance.plan = self.plan
        if commit:
            instance.save()
        return instance


class SubscriptionCustomerForm(forms.ModelForm):
    class Meta:
        model = SubscriptionCustomer
        fields = ["community", "person", "billing_account", "default_asset", "status", "label", "metadata"]
        widgets = {"metadata": forms.Textarea(attrs={"rows": 3})}


class SubscriptionCreateForm(forms.ModelForm):
    pay_now = forms.BooleanField(required=False, initial=False, label=_("Pay first invoice now"))

    class Meta:
        model = Subscription
        fields = ["customer", "plan", "price", "starts_at", "metadata"]
        widgets = {"starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}), "metadata": forms.Textarea(attrs={"rows": 3})}

    def clean(self):
        cleaned = super().clean()
        plan = cleaned.get("plan")
        price = cleaned.get("price")
        customer = cleaned.get("customer")
        if plan and price and price.plan_id != plan.id:
            self.add_error("price", _("Price must belong to the selected plan."))
        if plan and customer and customer.community_id != plan.community_id:
            self.add_error("plan", _("Plan must belong to the customer community."))
        return cleaned


class UsageRecordForm(forms.Form):
    subscription = forms.ModelChoiceField(queryset=Subscription.objects.select_related("plan", "customer"))
    feature_code = forms.CharField(max_length=100)
    quantity = forms.DecimalField(max_digits=24, decimal_places=6, min_value=Decimal("0.000001"))
    unit = forms.CharField(max_length=32, required=False)
    note = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def save(self):
        return record_usage(
            subscription=self.cleaned_data["subscription"],
            feature_code=self.cleaned_data["feature_code"],
            quantity=self.cleaned_data["quantity"],
            unit=self.cleaned_data.get("unit") or "",
            note=self.cleaned_data.get("note") or "",
        )


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = SubscriptionInvoice
        fields = ["due_at", "discount_base_units", "tax_base_units", "metadata"]
        widgets = {"due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}), "metadata": forms.Textarea(attrs={"rows": 3})}
