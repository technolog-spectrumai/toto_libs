from django import forms
from django.conf import settings


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

class SyncAppsForm(forms.Form):
    apps = forms.MultipleChoiceField(
        choices=[(app, app) for app in settings.APPS_TO_SYNC],
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label="Select apps to sync"
    )

    api_url = forms.CharField(
        required=True,
        label="Remote API URL",
        help_text="Address of the remote sync endpoint"
    )

    def __init__(self, *args, **kwargs):
        apps_choices = kwargs.pop("apps_choices", [])
        initial_api_url = kwargs.pop("initial_api_url", "")

        super().__init__(*args, **kwargs)

        self.fields["apps"].choices = [(a, a) for a in apps_choices]
        self.fields["api_url"].initial = initial_api_url
