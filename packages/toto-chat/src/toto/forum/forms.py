"""The two forms on the Cleanup page."""

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import ForumRetentionPolicy


class RetentionSettingsForm(forms.ModelForm):
    class Meta:
        model = ForumRetentionPolicy
        fields = ("enabled", "retention_days")
        labels = {
            "enabled": _("Delete old messages automatically"),
            "retention_days": _("Keep messages for"),
        }


class ConfirmCleanupForm(forms.Form):
    """Deliberately awkward, like ambrosia's destroy-workspace form.

    An irreversible thing should cost a sentence to ask for. Typing the word
    is not security — the endpoint re-checks staff — it is a pause between
    meaning to and doing.
    """

    WORD = "DELETE"

    confirm = forms.CharField(
        label=_("Type DELETE to confirm"),
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )

    def clean_confirm(self):
        value = (self.cleaned_data.get("confirm") or "").strip()
        if value != self.WORD:
            raise forms.ValidationError(
                _("Type %(word)s exactly to confirm.") % {"word": self.WORD})
        return value
