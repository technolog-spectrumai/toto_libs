import uuid

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


# ---------------------------------------------------------------------------
# My account (2026-09-30)
# ---------------------------------------------------------------------------

#: What an avatar may be, by what Pillow finds INSIDE the file, and the
#: extension it is stored under. The member's filename is never kept: a GIF
#: named ``me.html`` would otherwise be served from /media/ as a page.
AVATAR_FORMATS = {"JPEG": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp"}
AVATAR_MAX_PIXELS = 4096


def avatar_max_bytes() -> int:
    from django.conf import settings

    return int(getattr(settings, "SOCIALHUB_AVATAR_MAX_BYTES", 2 * 1024 * 1024))


class AccountProfileForm(forms.ModelForm):
    """The member's own profile, on My account. Four fields and nothing that
    decides access: communities, clearances, the patron and the map switch
    each have their own door, and none of them may ride along here."""

    class Meta:
        model = Person
        fields = ["display_name", "bio", "avatar", "phone"]
        labels = {
            "display_name": _("Display name"),
            "bio": _("About you"),
            "avatar": _("Avatar"),
            "phone": _("Phone"),
        }
        help_texts = {
            "avatar": _("A JPEG, PNG, GIF or WebP picture, at most 2 MB."),
        }
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 4}),
            "avatar": forms.ClearableFileInput(
                attrs={"accept": "image/jpeg,image/png,image/gif,image/webp"}),
        }

    def clean_display_name(self):
        name = (self.cleaned_data.get("display_name") or "").strip()
        if not name:
            raise forms.ValidationError(_("A display name is required."))
        return name

    def clean_avatar(self):
        """The platform's upload rules, applied to a picture that never enters
        the vault: the size cap, the host's refused types, and the antivirus
        door (`toto.vault.scanning`) — which today answers "not scanned" for
        a raster image, since there is no image scanner, and will screen it the
        day there is one without this form changing."""
        from django.core.files.uploadedfile import UploadedFile

        avatar = self.cleaned_data.get("avatar")
        if not isinstance(avatar, UploadedFile):
            return avatar  # unchanged, or False for "clear"
        if avatar.size > avatar_max_bytes():
            raise forms.ValidationError(
                _("The picture is larger than %(mb)s MB.")
                % {"mb": avatar_max_bytes() // (1024 * 1024)})
        image = getattr(avatar, "image", None)  # set by forms.ImageField
        extension = AVATAR_FORMATS.get(getattr(image, "format", "") or "")
        if extension is None:
            raise forms.ValidationError(
                _("Only JPEG, PNG, GIF or WebP pictures can be an avatar."))
        width, height = image.size
        if width > AVATAR_MAX_PIXELS or height > AVATAR_MAX_PIXELS:
            raise forms.ValidationError(
                _("The picture is larger than %(px)s pixels on a side.")
                % {"px": AVATAR_MAX_PIXELS})

        from toto.vault import scanning
        from toto.vault.models import VaultFile, refused_file_types

        stored_name = f"{uuid.uuid4().hex}.{extension}"
        file_type = VaultFile.detect_type(avatar.content_type or "", stored_name)
        if file_type in refused_file_types():
            raise forms.ValidationError(
                _("This host does not accept %(type)s files.") % {"type": file_type})
        avatar.seek(0)
        verdict = scanning.scan(avatar.read(), file_type=file_type, filename=stored_name)
        avatar.seek(0)
        if not verdict.ok:
            raise forms.ValidationError(
                _("The picture was refused: %(detail)s")
                % {"detail": verdict.detail or verdict.reason})
        avatar.name = stored_name
        return avatar


def time_zone_choices():
    from toto.people.models import time_zone_names

    return [("", _("Platform default"))] + [(name, name) for name in sorted(time_zone_names())]


class TimeZoneForm(forms.Form):
    """Blank is a real answer: the platform's own zone, followed if it moves."""

    timezone = forms.ChoiceField(label=_("Time zone"), required=False,
                                 choices=time_zone_choices)
