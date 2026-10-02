import io
import threading
import uuid
from contextlib import contextmanager

from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from toto.people.models import Person

User = get_user_model()
from toto.socialhub.models import (
    CommunityNewsPost,
    CommunityNewsTopic,
    MembershipApplication,
    ReferenceRequest,
)
from toto.verbena.forms import apply_oya_field_styles
from toto.verbena.widgets import use_local_trix


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
            'placeholder': _('Choose a username'),
            'autocomplete': 'username',
        }),
        help_text=_("You'll use this to log in once your application is approved."),
    )

    # The privacy notice (2026-10-01, RODO): the version shown rides along in
    # a hidden field so the one recorded is the one the applicant read — a
    # version published while the page was open is shown before it is
    # accepted, never accepted unseen.
    privacy_version = forms.IntegerField(widget=forms.HiddenInput, required=False)
    privacy_accept = forms.BooleanField(
        required=True,
        label=_("I have read the privacy notice and accept it."),
        error_messages={"required": _("Please read the privacy notice and tick the box to apply.")},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from toto.socialhub.models import PrivacyNotice

        self.privacy_notice = PrivacyNotice.current()
        if self.privacy_notice is not None:
            self.fields["privacy_version"].initial = self.privacy_notice.version
        # The lapsed application this one renews, if any (clean_email).
        self.renewing = None

    def clean_email(self):
        email = self.cleaned_data["email"]
        # Compared without case (2026-10-01): the column is unique only as
        # typed, so "Ann@…" beside "ann@…" made a second application, and a
        # second account, for one mailbox.
        lapsed = (MembershipApplication.objects.filter(email__iexact=email)
                  .order_by("pk").first())
        if lapsed is not None:
            from toto.socialhub.applications import renewable

            if renewable(lapsed):
                # (2026-10-01) An application whose week ran out before its
                # applicant got in no longer holds its address: applying
                # again renews that row (the view, applications.renew), so the
                # form is checked against it rather than refusing the address
                # as taken — which left the applicant stuck for good.
                self.renewing = lapsed
                self.instance = lapsed
            elif lapsed.email != email:
                # The model's own unique check sees only the exact spelling.
                raise lapsed.unique_error_message(MembershipApplication, ("email",))
        return email

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        taken = User.objects.filter(username__iexact=username)
        # Nor another account's e-mail address (2026-10-02, the crown bug
        # hunt): its owner typing their address where the username goes would
        # be trying THIS account, whose "Recent sign-ins" would show them
        # their address and browser. The applicant's own address is theirs.
        email = (self.cleaned_data.get("email") or "").strip()
        if username.casefold() != email.casefold():
            taken = taken | User.objects.filter(email__iexact=username)
        if self.renewing is not None:
            from toto.socialhub.applications import applicant_account

            # The lapsed attempt's own account is the applicant's to name again.
            reused = applicant_account(self.renewing)
            if reused is not None:
                taken = taken.exclude(pk=reused.pk)
        if taken.exists():
            raise forms.ValidationError(_("This username is already taken."))
        return username

    def clean(self):
        cleaned = super().clean()
        notice = self.privacy_notice
        if notice is None:
            # Nothing to accept means nothing to tell an applicant what is
            # done with their data: applications wait for a published notice.
            raise forms.ValidationError(
                _("Applications are closed until a privacy notice is published."))
        if cleaned.get("privacy_accept") and cleaned.get("privacy_version") != notice.version:
            self.add_error("privacy_accept", _(
                "The privacy notice has changed since this page was opened. "
                "Please read the current version and tick the box again."))
            # Drawn again with the current version and the box clear.
            self.data = self.data.copy()
            self.data[self.add_prefix("privacy_version")] = str(notice.version)
            self.data.pop(self.add_prefix("privacy_accept"), None)
        return cleaned

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
                'placeholder': _('Email address')
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
            'placeholder': _('Enter your verification code')
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
            'placeholder': _('Choose a password (optional)'),
            'autocomplete': 'new-password',
        }),
        help_text=_("Optional — set a password now so you can log in as soon as your application is approved."),
    )
    # Typed twice, and through AUTH_PASSWORD_VALIDATORS (2026-10-02, the crown
    # bug hunt): this field was bare — `1` or `password` was set, and the
    # account signed in with it once admitted — while every other door that
    # sets a password asks them. Django's own words, so its catalogue
    # translates them.
    password2 = forms.CharField(
        required=False,
        label=_("Password confirmation"),
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text=_("Enter the same password as before, for verification."),
    )

    def __init__(self, *args, application=None, **kwargs):
        super().__init__(*args, **kwargs)
        #: The account the password goes on: the one still waiting for its
        #: acceptance (``applications.applicant_account``), or None.
        self.applicant = None
        if application:
            from toto.socialhub.applications import applicant_account

            self.fields['referrer'].queryset = Person.objects.filter(
                communities=application.community
            )
            self.applicant = applicant_account(application, waiting_only=True)
        self._application = application

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password") or ""
        again = cleaned.get("password2") or ""
        if not password and not again:
            return cleaned
        if password != again:
            self.add_error("password2", forms.ValidationError(
                _("The two password fields didn’t match."), code="password_mismatch"))
            return cleaned
        from django.contrib.auth import password_validation

        # The validators compare it with the account it is for — its
        # username and address — or, when none waits any more, with the
        # address this application was made with.
        user = self.applicant or User(email=getattr(self._application, "email", ""))
        try:
            password_validation.validate_password(password, user=user)
        except forms.ValidationError as error:
            self.add_error("password", error)
        return cleaned

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
                'placeholder': _('Write a short endorsement message (optional)'),
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
            "title": forms.TextInput(attrs={"placeholder": _("Optional headline")}),
            "topics": forms.SelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["content"].required = True
        # Trix from the image, not unpkg (2026-10-01, 37c.20).
        use_local_trix(self.fields)
        self.fields["topics"].queryset = CommunityNewsTopic.objects.order_by("name")
        apply_oya_field_styles(self.fields, skip={"content"})


# ---------------------------------------------------------------------------
# My account (2026-09-30)
# ---------------------------------------------------------------------------

#: What an avatar may be, by what Pillow finds INSIDE the file, and the
#: extension it is stored under. The member's filename is never kept: a GIF
#: named ``me.html`` would otherwise be served from /media/ as a page. A
#: camera's MPO — a JPEG with a second picture behind the first, which Pillow
#: names apart — is stored as the JPEG every browser shows (2026-10-01,
#: 37c.24); it was refused until then, as a format off the list.
AVATAR_FORMATS = {"JPEG": "jpg", "MPO": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp"}
AVATAR_MAX_PIXELS = 4096

#: The qualities a JPEG or WebP avatar is encoded at, best first; the first
#: that fits under the size cap is stored (2026-10-01).
AVATAR_QUALITIES = (90, 80, 70)

#: A format Pillow reads that a picture is drawn again in another one: an
#: MPO's second picture is not kept, so what is left is a plain JPEG.
REDRAWN_AS = {"MPO": "JPEG"}


def avatar_max_bytes() -> int:
    from django.conf import settings

    return int(getattr(settings, "SOCIALHUB_AVATAR_MAX_BYTES", 2 * 1024 * 1024))


#: Held while one redraw has Pillow's process-wide switch turned off
#: (``_pixels_read_whole``), so two redraws never put back each other's.
_STRICT_DECODE = threading.Lock()


@contextmanager
def _pixels_read_whole():
    """Pillow refuses pixels cut short only while ``PIL.ImageFile.
    LOAD_TRUNCATED_IMAGES`` is off — one switch for the whole process, which
    WeasyPrint turns on for good the moment it is imported
    (``weasyprint/images.py``; Aralia's renderer imports it). With it on, a
    JPEG cut in half was decoded as half a picture over grey and stored
    (2026-10-02, 41.4: the gate imports the renderer's tests first, and both
    doors' "cut short" tests failed there). So the redraw decodes with the
    switch off, and puts it back as it found it."""
    from PIL import ImageFile

    with _STRICT_DECODE:
        was = ImageFile.LOAD_TRUNCATED_IMAGES
        ImageFile.LOAD_TRUNCATED_IMAGES = False
        try:
            yield
        finally:
            ImageFile.LOAD_TRUNCATED_IMAGES = was


def redraw_picture(data: bytes, image_format: str, max_bytes: int) -> bytes | None:
    """The picture drawn again from its pixels alone (2026-10-01).

    A photo says where it was taken and with what — EXIF with the GPS
    position and the camera, often XMP, an ICC profile, a comment — and
    stored as uploaded, /media/ handed all of it to anyone shown the picture.
    So Pillow decodes the first frame, turns it as its EXIF orientation says
    (the picture stands as the member saw it), and encodes a new file in the
    same format from the pixels (an MPO as a JPEG, ``REDRAWN_AS``): only a
    palette's transparency comes along. An animated GIF or WebP keeps its
    first frame.

    ``max_bytes`` holds for what is stored too: a JPEG or WebP is tried at
    each of ``AVATAR_QUALITIES`` until one fits; None when none does. Raises
    whatever Pillow raises for pixels it cannot decode — a file cut short
    included, whatever another library set Pillow to forgive
    (``_pixels_read_whole``). Avatars come here through ``reencode_avatar``,
    and the host's Trix attachments directly (37c.24).
    """
    from PIL import Image, ImageOps

    image_format = REDRAWN_AS.get(image_format, image_format)
    with _pixels_read_whole(), Image.open(io.BytesIO(data)) as source:
        source.load()  # every pixel of the first frame, decoded here
        picture = ImageOps.exif_transpose(source)
    # Everything else in info is metadata, and the encoders write some of it
    # back on their own (a JPEG's comment, a GIF's, a PNG's ICC profile).
    picture.info = {key: picture.info[key] for key in ("transparency",) if key in picture.info}
    if image_format == "JPEG" and picture.mode not in ("L", "RGB"):
        picture = picture.convert("RGB")  # a CMYK print scan
    qualities = AVATAR_QUALITIES if image_format in ("JPEG", "WEBP") else (None,)
    for quality in qualities:
        buffer = io.BytesIO()
        picture.save(buffer, format=image_format,
                     **({"quality": quality} if quality else {}))
        if buffer.tell() <= max_bytes:
            return buffer.getvalue()
    return None


def reencode_avatar(data: bytes, image_format: str) -> bytes | None:
    """An avatar drawn again without its metadata (``redraw_picture``), under
    the avatar's size cap."""
    return redraw_picture(data, image_format, avatar_max_bytes())


def clean_avatar_upload(avatar):
    """The platform's upload rules, applied to a picture that never enters
    the vault: the size cap, the host's refused types, and the antivirus
    door (`toto.vault.scanning`) — which today answers "not scanned" for
    a raster image, since there is no image scanner, and will screen it the
    day there is one without this changing. What is stored is the picture
    drawn again without its metadata (``reencode_avatar``), under a name of
    ours. My account's profile form and the admin's Person form both clean
    an avatar here."""
    from django.core.files.uploadedfile import SimpleUploadedFile, UploadedFile

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
    data = avatar.read()
    verdict = scanning.scan(data, file_type=file_type, filename=stored_name)
    if not verdict.ok:
        raise forms.ValidationError(
            _("The picture was refused: %(detail)s")
            % {"detail": verdict.detail or verdict.reason})
    try:
        data = reencode_avatar(data, image.format)
    except Exception as exc:  # noqa: BLE001 - Pillow's decoders share no base error
        # The header read well enough for ImageField; the pixels did not (a
        # file cut short, say).
        raise forms.ValidationError(forms.ImageField.default_error_messages["invalid_image"],
                                    code="invalid_image") from exc
    if data is None:
        raise forms.ValidationError(
            _("The picture is larger than %(mb)s MB.")
            % {"mb": avatar_max_bytes() // (1024 * 1024)})
    return SimpleUploadedFile(stored_name, data, content_type=avatar.content_type)


class AccountProfileForm(forms.ModelForm):
    """The member's own profile, on My account. Four fields, the two switches
    saying whether other members see the e-mail address and the phone number
    (2026-10-01, 37c.25; off by default, `contact_access`), and nothing that
    decides access: communities, clearances, the patron and the map switch
    each have their own door, and none of them may ride along here."""

    class Meta:
        model = Person
        fields = ["display_name", "bio", "avatar", "phone", "show_phone", "show_email"]
        labels = {
            "display_name": _("Display name"),
            "bio": _("About you"),
            "avatar": _("Avatar"),
            "phone": _("Phone"),
            "show_phone": _("Show my phone number to other members"),
            "show_email": _("Show my e-mail address to other members"),
        }
        help_texts = {
            "avatar": _("A JPEG, PNG, GIF or WebP picture, at most 2 MB."),
            "show_phone": _("When this is off, only you and the administrators see your "
                            "phone number."),
            "show_email": _("When this is off, only you and the administrators see your "
                            "e-mail address."),
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
        return clean_avatar_upload(self.cleaned_data.get("avatar"))


def time_zone_choices():
    from toto.people.models import time_zone_names

    return [("", _("Platform default"))] + [(name, name) for name in sorted(time_zone_names())]


class TimeZoneForm(forms.Form):
    """Blank is a real answer: the platform's own zone, followed if it moves."""

    timezone = forms.ChoiceField(label=_("Time zone"), required=False,
                                 choices=time_zone_choices)


class AccountEmailForm(forms.Form):
    """A new address for one's own account (2026-09-30); see ``email_change``.

    The current password too (review, 2026-10-01): the address is where a
    password reset goes, so moving it is taking the account. A session alone
    — left open, or stolen — must not be enough; the view counts a wrong one
    against the sign-in lockout.
    """

    new_email = forms.EmailField(label=_("New e-mail address"), max_length=254)
    password = forms.CharField(
        label=_("Your current password"), strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_new_email(self):
        from toto.socialhub.email_change import address_taken

        address = self.cleaned_data["new_email"].strip()
        if address.lower() == (self.user.email or "").strip().lower():
            raise forms.ValidationError(_("That is already your address."), code="same")
        if address_taken(address, self.user):
            raise forms.ValidationError(
                _("That address belongs to another account or application."), code="taken")
        return address

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError(_("Your current password was entered incorrectly."),
                                        code="password_incorrect")
        return password


class KeyStoreForm(forms.Form):
    """A passphrase for one's own key store, twice (2026-10-01).

    ``PasswordInput`` never renders its value back, so a refused form does
    not carry the passphrase into the page. See ``toto.gervazy.personal``.
    """

    passphrase = forms.CharField(
        label=_("Key store passphrase"), strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    passphrase2 = forms.CharField(
        label=_("The same passphrase again"), strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def clean_passphrase(self):
        from toto.gervazy.personal import PASSPHRASE_MIN_LENGTH

        passphrase = self.cleaned_data["passphrase"]
        if len(passphrase) < PASSPHRASE_MIN_LENGTH:
            raise forms.ValidationError(
                _("Use at least %(count)d characters.") % {"count": PASSPHRASE_MIN_LENGTH},
                code="short")
        return passphrase

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get("passphrase") and "passphrase2" in cleaned
                and cleaned["passphrase"] != cleaned["passphrase2"]):
            self.add_error("passphrase2", forms.ValidationError(
                _("The two passphrases do not match."), code="mismatch"))
        return cleaned
