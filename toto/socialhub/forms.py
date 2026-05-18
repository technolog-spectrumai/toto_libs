from django import forms
from trix_editor.widgets import TrixEditorWidget

from toto.people.models import Person
from toto.socialhub.models import (
    CommunityNewsPost,
    CommunityNewsTopic,
    MembershipApplication,
    ReferenceRequest,
)
from toto.verbena.forms import apply_oya_field_styles


class MembershipApplicationForm(forms.ModelForm):
    class Meta:
        model = MembershipApplication
        fields = ['email', 'community']
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
            self.fields['referrer'].queryset = Person.objects.filter(
                communities=application.community
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
