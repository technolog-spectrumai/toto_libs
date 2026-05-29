from django import forms
from django.utils.translation import gettext_lazy as _

from .models import UsageEvent, UsageMetric, UsageQuota

_INPUT = (
    "rounded border px-3 py-1.5 text-sm w-full "
    "bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-600 "
    "focus:outline-none focus:ring-1 focus:ring-indigo-500"
)
_SELECT = _INPUT
_TEXTAREA = _INPUT + " font-mono"
_CHECKBOX = "rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"


class UsageMetricForm(forms.ModelForm):
    class Meta:
        model = UsageMetric
        fields = ["code", "name", "namespace", "default_unit", "description", "is_active", "metadata"]
        widgets = {
            "code": forms.TextInput(attrs={"class": _INPUT}),
            "name": forms.TextInput(attrs={"class": _INPUT}),
            "namespace": forms.TextInput(attrs={"class": _INPUT}),
            "default_unit": forms.TextInput(attrs={"class": _INPUT}),
            "description": forms.Textarea(attrs={"class": _TEXTAREA, "rows": 3}),
            "is_active": forms.CheckboxInput(attrs={"class": _CHECKBOX}),
            "metadata": forms.Textarea(attrs={"class": _TEXTAREA, "rows": 3}),
        }


class UsageEventForm(forms.ModelForm):
    class Meta:
        model = UsageEvent
        fields = [
            "metric", "quantity", "unit", "occurred_at",
            "source_type", "source_id", "source_label",
            "subject_type", "subject_id", "subject_label",
            "idempotency_key", "description", "metadata",
        ]
        widgets = {
            "metric": forms.Select(attrs={"class": _SELECT}),
            "quantity": forms.NumberInput(attrs={"class": _INPUT, "step": "any"}),
            "unit": forms.TextInput(attrs={"class": _INPUT}),
            "occurred_at": forms.DateTimeInput(
                attrs={"class": _INPUT, "type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "source_type": forms.TextInput(attrs={"class": _INPUT}),
            "source_id": forms.TextInput(attrs={"class": _INPUT}),
            "source_label": forms.TextInput(attrs={"class": _INPUT}),
            "subject_type": forms.TextInput(attrs={"class": _INPUT}),
            "subject_id": forms.TextInput(attrs={"class": _INPUT}),
            "subject_label": forms.TextInput(attrs={"class": _INPUT}),
            "idempotency_key": forms.TextInput(attrs={"class": _INPUT}),
            "description": forms.Textarea(attrs={"class": _TEXTAREA, "rows": 2}),
            "metadata": forms.Textarea(attrs={"class": _TEXTAREA, "rows": 3}),
        }


class UsageQuotaForm(forms.ModelForm):
    class Meta:
        model = UsageQuota
        fields = [
            "code", "name", "metric",
            "subject_type", "subject_id", "subject_label",
            "limit_quantity", "unit", "period", "rolling_seconds", "mode",
            "starts_at", "ends_at", "is_active", "description", "metadata",
        ]
        widgets = {
            "code": forms.TextInput(attrs={"class": _INPUT}),
            "name": forms.TextInput(attrs={"class": _INPUT}),
            "metric": forms.Select(attrs={"class": _SELECT}),
            "subject_type": forms.TextInput(attrs={"class": _INPUT}),
            "subject_id": forms.TextInput(attrs={"class": _INPUT}),
            "subject_label": forms.TextInput(attrs={"class": _INPUT}),
            "limit_quantity": forms.NumberInput(attrs={"class": _INPUT, "step": "any"}),
            "unit": forms.TextInput(attrs={"class": _INPUT}),
            "period": forms.Select(attrs={"class": _SELECT}),
            "rolling_seconds": forms.NumberInput(attrs={"class": _INPUT}),
            "mode": forms.Select(attrs={"class": _SELECT}),
            "starts_at": forms.DateTimeInput(
                attrs={"class": _INPUT, "type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "ends_at": forms.DateTimeInput(
                attrs={"class": _INPUT, "type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "is_active": forms.CheckboxInput(attrs={"class": _CHECKBOX}),
            "description": forms.Textarea(attrs={"class": _TEXTAREA, "rows": 2}),
            "metadata": forms.Textarea(attrs={"class": _TEXTAREA, "rows": 3}),
        }
