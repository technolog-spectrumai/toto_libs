from django import forms


class BackupAppsForm(forms.Form):
    apps = forms.MultipleChoiceField(
        choices=[],
        widget=forms.CheckboxSelectMultiple(),
        required=True,
        label="Apps",
    )

    def __init__(self, *args, **kwargs):
        apps_choices = kwargs.pop("apps_choices", [])
        super().__init__(*args, **kwargs)
        self.fields["apps"].choices = [(a, a) for a in apps_choices]


class ApplyBackupForm(forms.Form):
    backup_file = forms.FileField(required=True, label="Backup ZIP")
    verify_signature = forms.BooleanField(required=False, initial=True, label="Verify signature")
    clear_existing = forms.BooleanField(
        required=False,
        initial=False,
        label="Clear existing data first",
        help_text="Deletes existing objects for imported models before restoring.",
    )
