"""Forms for editing limits.

Each app owns its own policy table, so there is no single model to build a
ModelForm against — :func:`policy_form_for` stamps one out per concrete model.
The fields are identical across apps because they all come from
``AbstractQuotaPolicy``; only the table differs.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

FIELDS = ("limit", "unit", "period", "mode", "active", "starts_at", "ends_at")

WIDGETS = {
    "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
    "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
}

HELP = {
    "mode": _("Block refuses the action. Track and warn only record it."),
    "period": _("Windows are calendar-aligned — a daily limit resets at midnight."),
    "starts_at": _("Optional. Before this, the policy does not apply."),
    "ends_at": _("Optional. From this moment, the policy stops applying."),
}


class BasePolicyForm(forms.ModelForm):
    """Shared behaviour; the model is bound by :func:`policy_form_for`."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, text in HELP.items():
            if name in self.fields:
                self.fields[name].help_text = text

    def clean_limit(self):
        limit = self.cleaned_data["limit"]
        if limit is not None and limit < 0:
            raise forms.ValidationError(_("A limit cannot be negative."))
        return limit

    def clean(self):
        cleaned = super().clean()
        starts, ends = cleaned.get("starts_at"), cleaned.get("ends_at")
        if starts and ends and ends <= starts:
            self.add_error("ends_at", _("The end must come after the start."))
        return cleaned


def policy_form_for(policy_model):
    """A ModelForm class for one app's concrete quota policy.

    One form, one row per metric. There was an ``include_user`` variant that
    added a user field for per-person overrides; it is gone, and so is the
    office ``limit_multiplier`` that briefly replaced it. Limits are flat.
    """
    return forms.modelform_factory(
        policy_model, form=BasePolicyForm, fields=list(FIELDS),
        widgets=dict(WIDGETS)
    )
