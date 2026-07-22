from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from trix_editor.widgets import TrixEditorWidget

from toto.people.models import Person

User = get_user_model()
from toto.socialhub.models import (
    CommunityNewsPost,
    CommunityNewsTopic,
    MembershipApplication,
    ReferenceRequest,
)
from toto.verbena.forms import apply_oya_field_styles


class MembershipApplicationForm(forms.ModelForm):
    # Login username, chosen by the applicant and distinct from their email. The
    # email remains the stable key for the application/verification flow; this is
    # what they type at the login form once approved.
    username = forms.CharField(
        max_length=150,
        label=_("Username"),
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300',
            'x-bind:class': "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
            'placeholder': 'Choose a username',
            'autocomplete': 'username',
        }),
        help_text=_("You'll use this to log in once your application is approved."),
    )

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError(_("This username is already taken."))
        return username

    class Meta:
        model = MembershipApplication
        fields = ['email', 'community']
        labels = {
            'email': _("Email"),
            'community': _("Community"),
        }
        widgets = {
            'email': forms.EmailInput(attrs={
                'class': 'w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300',
                'x-bind:class': "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
                'placeholder': 'Email address'
            }),
            'community': forms.Select(attrs={
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
        queryset=Person.objects.none(),
        label=_("Referrer"),
        widget=forms.Select(attrs={
            'class': 'w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300',
            'x-bind:class': "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'"
        }),
        required=True,
        help_text=_("Select the community member endorsing this application")
    )

    password = forms.CharField(
        required=False,
        label=_("Password"),
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Choose a password (optional)',
            'autocomplete': 'new-password',
        }),
        help_text=_("Optional — set a password now so you can log in as soon as your application is approved."),
    )

    def __init__(self, *args, application=None, **kwargs):
        super().__init__(*args, **kwargs)
        if application:
            self.fields['referrer'].queryset = Person.objects.filter(
                communities=application.community
            )

    class Meta:
        model = ReferenceRequest
        fields = ['referrer', 'message']
        labels = {
            'message': _("Message"),
        }
        widgets = {
            'message': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300',
                'x-bind:class': "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
                'placeholder': 'Write a short endorsement message (optional)',
                'rows': 4
            })
        }


def _community_news_fields():
    return ["title", "content", "author", "topics", "visibility"]


class CommunityNewsPostForm(forms.ModelForm):
    class Meta:
        model = CommunityNewsPost
        fields = _community_news_fields()
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Optional headline"}),
            "content": TrixEditorWidget(),
            "topics": forms.SelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["content"].required = True
        self.fields["topics"].queryset = CommunityNewsTopic.objects.order_by("name")
        apply_oya_field_styles(self.fields, skip={"content"})
