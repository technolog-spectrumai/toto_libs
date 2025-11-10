import base64

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


class ChallengeIdentityForm(forms.Form):
    identity_id = forms.UUIDField(
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300',
            'placeholder': 'Identity ID'
        })
    )

    def clean_identity_id(self):
        identity_id = self.cleaned_data["identity_id"]
        if not FederatedIdentity.objects.filter(pk=identity_id).exists():
            raise forms.ValidationError("Invalid Identity ID.")
        return identity_id


class ChallengeSignatureForm(forms.Form):
    identity_id = forms.UUIDField(widget=forms.HiddenInput())
    signature = forms.CharField(
        widget=forms.Textarea(attrs={
            'class': 'w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300',
            'placeholder': 'Paste signature here',
            'rows': 4
        })
    )

    def clean_signature(self):
        sig_str = self.cleaned_data["signature"].strip()

        # Try hex first
        try:
            return bytes.fromhex(sig_str)
        except Exception as e:
            pass

        # Try Base64 (with padding fix)
        try:
            missing_padding = len(sig_str) % 4
            if missing_padding:
                sig_str += "=" * (4 - missing_padding)
            return base64.b64decode(sig_str, validate=False)
        except Exception:
            raise forms.ValidationError("Signature must be valid hex or Base64.")

    def clean_identity_id(self):
        identity_id = self.cleaned_data["identity_id"]
        if not FederatedIdentity.objects.filter(id=identity_id).exists():
            raise forms.ValidationError("Invalid Identity ID.")
        return identity_id
