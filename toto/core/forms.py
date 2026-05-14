from django import forms


class LoginForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300',
            'x-bind:class': "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
            'placeholder': 'Username'
        })
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300',
            'x-bind:class': "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
            'placeholder': 'Password'
        })
    )

class BackupAppsForm(forms.Form):
    apps = forms.MultipleChoiceField(
        choices=[],
        widget=forms.CheckboxSelectMultiple,
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


class QueryExecForm(forms.Form):
    query = forms.CharField(
        required=True,
        label="Query",
        widget=forms.Textarea(attrs={
            "rows": 6,
            "style": "width:100%;font-family:monospace;",
            "placeholder": "show apps  |  show models  |  show platform",
        }),
    )
