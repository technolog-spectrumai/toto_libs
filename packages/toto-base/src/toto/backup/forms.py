from django import forms
from django.utils.translation import gettext_lazy as _


class BackupAppsForm(forms.Form):
    apps = forms.MultipleChoiceField(
        choices=[],
        widget=forms.CheckboxSelectMultiple(),
        required=True,
        label=_("Apps"),
    )

    def __init__(self, *args, **kwargs):
        apps_choices = kwargs.pop("apps_choices", [])
        super().__init__(*args, **kwargs)
        self.fields["apps"].choices = [(a, a) for a in apps_choices]


class ApplyBackupForm(forms.Form):
    backup_file = forms.FileField(required=True, label=_("Backup ZIP"))
    verify_signature = forms.BooleanField(required=False, initial=True, label=_("Verify signature"))
    clear_existing = forms.BooleanField(
        required=False,
        initial=False,
        label=_("Clear existing data first"),
        help_text=_("Deletes existing objects for imported models before restoring."),
    )
