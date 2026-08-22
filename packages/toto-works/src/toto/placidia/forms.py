"""Forms for contributing to a bounty and for reviewing what arrives."""

from django import forms
from django.utils.translation import gettext_lazy as _

from toto.kanban.models import ReviewVerdict

_INPUT = ("w-full rounded-lg border px-3 py-2 text-sm shadow-sm")
_DARK = ("darkMode ? 'bg-bubble-bg-dark border-accent-1' "
         ": 'bg-bubble-bg-light border-accent-2'")


class ContributionForm(forms.Form):
    """What a contributor sends: prose, files, and where/when it was observed.

    Files are chosen from the vault rather than uploaded here. That is not a
    shortcut — it is the platform's rule: bytes stay vault-governed, so
    downloads keep going through vault's own access rules and an antivirus
    scan has already happened at the door the file arrived by. The queryset is
    ``accessible_files(user, include_public=False)``: files this person has a
    CLAIM on, not merely ones they can read, because this attaches them.
    """

    notes = forms.CharField(
        label=_("What did you observe?"),
        widget=forms.Textarea(attrs={
            "rows": 5, "class": _INPUT, "x-bind:class": _DARK,
            "placeholder": _("Describe what you found, where, and anything a reviewer should know."),
        }),
        required=False,
    )
    observed_at = forms.DateTimeField(
        label=_("Observed at"),
        required=False,
        widget=forms.DateTimeInput(attrs={
            "type": "datetime-local", "class": _INPUT, "x-bind:class": _DARK}),
    )
    files = forms.ModelMultipleChoiceField(
        label=_("Attach files from your vault"),
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from toto.vault.filetree import accessible_files

        if user is not None:
            self.fields["files"].queryset = accessible_files(
                user, include_public=False)
        else:
            from toto.vault.models import VaultFile
            self.fields["files"].queryset = VaultFile.objects.none()


class ReviewForm(forms.Form):
    """One reviewer's verdict."""

    verdict = forms.ChoiceField(
        choices=ReviewVerdict.choices,
        widget=forms.RadioSelect,
        label=_("Your verdict"),
    )
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "rows": 3, "class": _INPUT, "x-bind:class": _DARK,
            "placeholder": _("Optional: why?"),
        }),
        label=_("Comment"),
    )
