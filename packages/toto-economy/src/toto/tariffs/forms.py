from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from toto.assets.models import Asset, LedgerAccount, to_base_units

from .models import BillingMetric, BillingUnit, RoundingMode, Tariff, TariffItem, TariffStatus, UsageRecord


class TariffForm(forms.ModelForm):
    class Meta:
        model = Tariff
        fields = [
            "name", "code", "status", "description", "owner", "default_asset",
            "source_type", "source_id", "metadata",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["default_asset"].queryset = Asset.objects.filter(active=True)
        # Empty is meaningful — it inherits the host's gas asset — so name it
        # rather than leaving Django's bare "---------".
        self.fields["default_asset"].empty_label = _("Host default (gas asset)")

    def clean_code(self):
        code = self.cleaned_data["code"].strip()
        if not code:
            raise forms.ValidationError(_("Code is required."))
        return code


class TariffItemForm(forms.ModelForm):
    price_per_unit_display = forms.DecimalField(
        max_digits=30,
        decimal_places=18,
        min_value=Decimal("0"),
        label=_("Price per unit (display)"),
        help_text=_("Human-readable price in asset display units"),
    )

    class Meta:
        model = TariffItem
        fields = [
            "metric", "name", "charged_asset", "price_per_unit_display",
            "unit", "unit_quantity", "receiving_account",
            "minimum_charge_base_units", "rounding_mode", "active", "metadata",
        ]
        widgets = {
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, tariff=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tariff = tariff
        self.fields["metric"].queryset = BillingMetric.objects.filter(active=True)
        # The asset this item ALREADY charges in stays selectable even if it has
        # since been deactivated, and that is what makes editing a price work.
        #
        # Narrowing this to `active=True` alone meant the bound value of an item
        # priced in a retired currency was "not one of the available choices" —
        # so saving failed on a field nobody had touched, the view re-rendered
        # at HTTP 200, and the price the person typed simply did not persist.
        # From the outside that is indistinguishable from a save that silently
        # does nothing, which is exactly how it was reported.
        assets = Asset.objects.filter(active=True)
        if self.instance.pk and self.instance.charged_asset_id:
            assets = Asset.objects.filter(
                Q(active=True) | Q(pk=self.instance.charged_asset_id))
        self.fields["charged_asset"].queryset = assets
        # A new item starts in the tariff's currency; overriding it here is the
        # per-metric choice.
        if tariff is not None and not self.instance.pk and not self.initial.get("charged_asset"):
            inherited = tariff.pricing_asset()
            if inherited is not None:
                self.fields["charged_asset"].initial = inherited.pk
        self.fields["receiving_account"].queryset = LedgerAccount.objects.filter(active=True)
        self.fields["unit"].queryset = BillingUnit.objects.filter(active=True)

    def clean(self):
        cleaned = super().clean()
        asset = cleaned.get("charged_asset")

        # The state a fresh platform is in until somebody engraves a currency:
        # this dropdown has NO options, so every submit fails on it and no price
        # can ever be saved. "Select a valid choice" against an empty select is
        # a true statement about the wrong problem.
        if not self.fields["charged_asset"].queryset.exists():
            raise forms.ValidationError(_(
                "There are no currencies on this platform yet, so there is "
                "nothing to price this in. Create one under Assets first — a "
                "price without a currency is not a price."))

        price = cleaned.get("price_per_unit_display")
        metric = cleaned.get("metric")

        if asset and price is not None:
            try:
                cleaned["price_per_unit_base_units"] = to_base_units(price, asset.decimals)
            except (InvalidOperation, Exception) as exc:
                raise forms.ValidationError({"price_per_unit_display": str(exc)}) from exc

        uq = cleaned.get("unit_quantity")
        if uq is not None and uq <= 0:
            self.add_error("unit_quantity", _("Unit quantity must be > 0."))

        if self.tariff and metric:
            qs = TariffItem.objects.filter(tariff=self.tariff, metric=metric)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("metric", _("This metric already exists in this tariff."))

        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        # `.get`, not `[...]`: clean() sets this key only when BOTH an asset and
        # a price survived validation, so subscripting it raised KeyError on the
        # paths where it did not. `TariffItem.save()` derives the same number
        # from the display price anyway — this only carries clean()'s answer
        # forward when it has one.
        derived = self.cleaned_data.get("price_per_unit_base_units")
        if derived is not None:
            instance.price_per_unit_base_units = derived
        if self.tariff and not instance.tariff_id:
            instance.tariff = self.tariff
        if commit:
            instance.save()
        return instance


class UsageRecordForm(forms.ModelForm):
    class Meta:
        model = UsageRecord
        fields = [
            "tariff", "payer_account", "metric_code", "quantity", "unit",
            "source_type", "source_id", "occurred_at", "metadata",
        ]
        widgets = {
            "occurred_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tariff"].queryset = Tariff.objects.filter(status=TariffStatus.ACTIVE)
        self.fields["payer_account"].queryset = LedgerAccount.objects.filter(active=True)

    def clean_quantity(self):
        qty = self.cleaned_data["quantity"]
        if qty <= 0:
            raise forms.ValidationError(_("Quantity must be > 0."))
        return qty


class UsageSimulationForm(forms.Form):
    metric_code = forms.CharField(
        max_length=100,
        label=_("Metric code"),
        help_text=_("e.g. ai.input_tokens, storage.mb_hour"),
    )
    quantity = forms.DecimalField(
        max_digits=30,
        decimal_places=10,
        min_value=Decimal("0.0000000001"),
        label=_("Quantity"),
    )
    unit = forms.CharField(
        max_length=100,
        required=False,
        label=_("Unit"),
        help_text=_("e.g. request, token, mb_hour"),
    )

