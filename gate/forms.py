from django import forms
from community.models import MembershipApplication, ReferenceRequest, CommunityMember, Community
from federal.models import FederatedIdentity


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


class ChallengeLoginForm(forms.Form):
    identity_id = forms.UUIDField(
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300',
            'x-bind:class': "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
            'placeholder': 'Identity ID'
        })
    )
    signature = forms.CharField(
        widget=forms.Textarea(attrs={
            'class': 'w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300',
            'x-bind:class': "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
            'placeholder': 'Paste your signature here',
            'rows': 4,   # makes it taller
            'cols': 80   # makes it wider
        })
    )

    def clean_identity_id(self):
        identity_id = self.cleaned_data["identity_id"]
        # Ensure the identity exists
        if not FederatedIdentity.objects.filter(pk=identity_id).exists():
            raise forms.ValidationError("Invalid Identity ID.")
        return identity_id