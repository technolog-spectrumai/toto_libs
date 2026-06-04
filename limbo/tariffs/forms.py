from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import forms
from django.utils.translation import gettext_lazy as _

from toto.assets.models import Asset, LedgerAccount, to_base_units

from .models import BillingMetric, BillingUnit, RoundingMode, Tariff, TariffItem, TariffStatus, UsageRecord


class TariffForm(forms.ModelForm):
    class Meta:
        model = Tariff
        fields = ["name", "code", "status", "description", "owner", "source_type", "source_id", "metadata"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }

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
        self.fields["charged_asset"].queryset = Asset.objects.filter(active=True)
        self.fields["receiving_account"].queryset = LedgerAccount.objects.filter(active=True)
        self.fields["unit"].queryset = BillingUnit.objects.filter(active=True)

    def clean(self):
        cleaned = super().clean()
        asset = cleaned.get("charged_asset")
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
        instance.price_per_unit_base_units = self.cleaned_data["price_per_unit_base_units"]
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


class ApplyUsageStatementForm(forms.Form):
    usage_file = forms.ModelChoiceField(
        queryset=None,
        label=_("Usage Statement File (YAML)"),
        help_text=_("Select a VaultFile containing a usage_statement YAML."),
    )
    tariff = forms.ModelChoiceField(
        queryset=None,
        label=_("Tariff"),
        help_text=_("Tariff to apply for pricing."),
    )
    issued_to = forms.ModelChoiceField(
        queryset=None,
        label=_("Issued To"),
        help_text=_("User this invoice is addressed to."),
    )
    due_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label=_("Due Date"),
    )
    title = forms.CharField(
        max_length=255,
        required=False,
        label=_("Invoice Title Override"),
        help_text=_("Leave blank to auto-generate from the usage statement."),
    )
    description = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
        label=_("Description"),
    )
    override_source_app = forms.CharField(
        max_length=100,
        required=False,
        label=_("Override Source App"),
        help_text=_("Override the source_app detected from the YAML."),
    )
    override_subject_label = forms.CharField(
        max_length=255,
        required=False,
        label=_("Override Subject Label"),
        help_text=_("Override the subject label detected from the YAML."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model
        from toto.vault.models import VaultFile
        User = get_user_model()
        self.fields["usage_file"].queryset = VaultFile.objects.filter(file_type="yaml").order_by("-uploaded_at")
        self.fields["tariff"].queryset = Tariff.objects.filter(status=TariffStatus.ACTIVE).order_by("name")
        self.fields["issued_to"].queryset = User.objects.filter(is_active=True).order_by("username")

        _css = "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-1 oya-form-field"
        for field in self.fields.values():
            field.widget.attrs["class"] = _css

    def clean(self):
        cleaned = super().clean()
        usage_file = cleaned.get("usage_file")
        tariff = cleaned.get("tariff")

        if usage_file:
            try:
                from toto.tariffs.usage_statement_services import read_usage_statement_from_vault_file
                read_usage_statement_from_vault_file(usage_file)
            except Exception as exc:
                self.add_error("usage_file", _("YAML validation failed: ") + str(exc))

        if not tariff:
            self.add_error("tariff", _("Please select a tariff."))

        if not cleaned.get("issued_to"):
            self.add_error("issued_to", _("Please select a recipient."))

        return cleaned
