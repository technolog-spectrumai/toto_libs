from django import forms
from oya.models import MembershipApplication, ReferenceRequest, CommunityMember


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


class MembershipApplicationForm(forms.ModelForm):
    class Meta:
        model = MembershipApplication
        fields = ['email', 'branch']
        widgets = {
            'email': forms.EmailInput(attrs={
                'class': 'w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300',
                'x-bind:class': "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
                'placeholder': 'Email address'
            }),
            'branch': forms.Select(attrs={
                'class': 'w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300',
                'x-bind:class': "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'"
            })
        }


class CodeVerificationForm(forms.Form):
    code = forms.CharField(
        max_length=10,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300',
            'x-bind:class': "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
            'placeholder': 'Enter your verification code'
        })
    )

class ReferenceRequestForm(forms.ModelForm):
    referrer = forms.ModelChoiceField(
        queryset=CommunityMember.objects.none(),
        widget=forms.Select(attrs={
            'class': 'w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300',
            'x-bind:class': "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'"
        }),
        required=True,
        help_text="Select the community member endorsing this application"
    )

    def __init__(self, *args, application=None, **kwargs):
        super().__init__(*args, **kwargs)
        if application:
            self.fields['referrer'].queryset = CommunityMember.objects.filter(
                membership__in=[application.branch]
            )

    class Meta:
        model = ReferenceRequest
        fields = ['referrer', 'message']
        widgets = {
            'message': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300',
                'x-bind:class': "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
                'placeholder': 'Write a short endorsement message (optional)',
                'rows': 4
            })
        }



