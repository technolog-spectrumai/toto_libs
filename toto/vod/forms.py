from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import VodCollection, VodVideo
from .services import create_video_from_upload

_INPUT = (
    "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20"
)
_XBIND = (
    "darkMode "
    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
    ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
)


def _text(placeholder=""):
    return forms.TextInput(attrs={"placeholder": placeholder, "class": _INPUT, "x-bind:class": _XBIND})


def _textarea(rows=3, placeholder=""):
    return forms.Textarea(attrs={"rows": rows, "placeholder": placeholder, "class": _INPUT, "x-bind:class": _XBIND})


def _select():
    return forms.Select(attrs={"class": _INPUT, "x-bind:class": _XBIND})


def _number(placeholder=""):
    return forms.NumberInput(attrs={"placeholder": placeholder, "class": _INPUT, "x-bind:class": _XBIND})


class VodCollectionForm(forms.ModelForm):
    class Meta:
        model = VodCollection
        fields = ["title", "slug", "description", "access_mode", "allow_downloads", "position"]
        widgets = {
            "title": _text(_("Collection title")),
            "slug": _text(_("url-slug")),
            "description": _textarea(3, _("Describe this collection…")),
            "access_mode": _select(),
            "position": _number("0"),
        }


class VodVideoForm(forms.ModelForm):
    class Meta:
        model = VodVideo
        fields = [
            "title", "slug", "description", "status",
            "duration_seconds", "position", "tags", "hls_segment_seconds",
        ]
        widgets = {
            "title": _text(_("Video title")),
            "slug": _text(_("url-slug")),
            "description": _textarea(3),
            "status": _select(),
            "tags": _textarea(2, _('["tag1", "tag2"]')),
            "duration_seconds": _number(),
            "position": _number("0"),
            "hls_segment_seconds": _number("6"),
        }


class VodUploadForm(forms.Form):
    collection = forms.ModelChoiceField(
        queryset=VodCollection.objects.all(),
        widget=_select(),
    )
    title = forms.CharField(max_length=240, widget=_text(_("Video title")))
    description = forms.CharField(
        widget=_textarea(3, _("Optional description…")),
        required=False,
    )
    video_file = forms.FileField(label=_("Video file"))
    poster_file = forms.FileField(label=_("Poster image"), required=False)
    status = forms.ChoiceField(
        choices=VodVideo.Status.choices,
        initial=VodVideo.Status.DRAFT,
        widget=_select(),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["collection"].queryset = VodCollection.objects.select_related("owner", "bucket").all()

    def clean_video_file(self):
        f = self.cleaned_data["video_file"]
        name = (getattr(f, "name", "") or "").lower()
        content_type = (getattr(f, "content_type", "") or "").lower()
        if not (content_type.startswith("video/") or name.endswith((".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".ts", ".m2ts"))):
            raise forms.ValidationError(_("Upload a video file."))
        return f

    def save(self):
        return create_video_from_upload(
            collection=self.cleaned_data["collection"],
            uploaded_file=self.cleaned_data["video_file"],
            poster_file=self.cleaned_data.get("poster_file"),
            owner=self.user,
            title=self.cleaned_data["title"],
            description=self.cleaned_data.get("description") or "",
            status=self.cleaned_data.get("status") or VodVideo.Status.DRAFT,
            build_hls=False,
        )
