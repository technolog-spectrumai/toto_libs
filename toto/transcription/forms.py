from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import SpeechModel, TranscriptCollection, TranscriptSource, TranscriptionJob
from .services import create_source_from_upload, writable_collections_for_user

_INPUT = "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20"
_XBIND = (
    "darkMode "
    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark placeholder-text-main-dark/40' "
    ": 'border-accent-2 bg-primary-bg-light text-text-main-light placeholder-text-main-light/40'"
)


def _text(placeholder=""):
    return forms.TextInput(attrs={"placeholder": placeholder, "class": _INPUT, "x-bind:class": _XBIND})


def _textarea(rows=3, placeholder=""):
    return forms.Textarea(attrs={"rows": rows, "placeholder": placeholder, "class": _INPUT, "x-bind:class": _XBIND})


def _select():
    return forms.Select(attrs={"class": _INPUT, "x-bind:class": _XBIND})


def _number(placeholder=""):
    return forms.NumberInput(attrs={"placeholder": placeholder, "class": _INPUT, "x-bind:class": _XBIND})


def _checkbox():
    return forms.CheckboxInput(attrs={"class": "rounded border-current/20"})


class TranscriptCollectionForm(forms.ModelForm):
    class Meta:
        model = TranscriptCollection
        fields = ["title", "slug", "description", "access_mode", "allow_downloads", "position"]
        widgets = {
            "title": _text(_("Collection title")),
            "slug": _text(_("url-slug")),
            "description": _textarea(3, _("Describe this transcript collection…")),
            "access_mode": _select(),
            "position": _number("0"),
            "allow_downloads": _checkbox(),
        }


class TranscriptSourceForm(forms.ModelForm):
    class Meta:
        model = TranscriptSource
        fields = ["title", "slug", "description", "status", "language", "duration_seconds", "position", "tags"]
        widgets = {
            "title": _text(_("Source title")),
            "slug": _text(_("url-slug")),
            "description": _textarea(3),
            "status": _select(),
            "language": _text(_("en, pl, de…")),
            "duration_seconds": _number(),
            "position": _number("0"),
            "tags": _textarea(2, '["meeting", "lecture"]'),
        }


class TranscriptUploadForm(forms.Form):
    collection = forms.ModelChoiceField(queryset=TranscriptCollection.objects.none(), widget=_select())
    title = forms.CharField(max_length=240, widget=_text(_("Recording title")))
    description = forms.CharField(widget=_textarea(3, _("Optional description…")), required=False)
    language = forms.CharField(max_length=16, required=False, widget=_text(_("Optional language, e.g. en or pl")))
    media_file = forms.FileField(label=_("Audio or video file"))
    status = forms.ChoiceField(choices=TranscriptSource.Status.choices, initial=TranscriptSource.Status.DRAFT, widget=_select())

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["collection"].queryset = writable_collections_for_user(user)

    def clean_media_file(self):
        f = self.cleaned_data["media_file"]
        name = (getattr(f, "name", "") or "").lower()
        content_type = (getattr(f, "content_type", "") or "").lower()
        audio_ext = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".webm")
        video_ext = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".ts", ".m2ts")
        if not (content_type.startswith("audio/") or content_type.startswith("video/") or name.endswith(audio_ext + video_ext)):
            raise forms.ValidationError(_("Upload an audio or video file."))
        return f

    def save(self):
        return create_source_from_upload(
            collection=self.cleaned_data["collection"],
            uploaded_file=self.cleaned_data["media_file"],
            owner=self.user,
            title=self.cleaned_data["title"],
            description=self.cleaned_data.get("description") or "",
            language=self.cleaned_data.get("language") or "",
            status=self.cleaned_data.get("status") or TranscriptSource.Status.DRAFT,
        )


class SpeechModelForm(forms.ModelForm):
    class Meta:
        model = SpeechModel
        fields = ["name", "slug", "backend", "description", "model_size", "device", "compute_type", "beam_size", "language", "is_active"]
        widgets = {
            "name": _text(_("e.g. Small Fast")),
            "slug": _text(_("small-fast")),
            "backend": _select(),
            "description": _textarea(2),
            "model_size": _text(_("tiny / base / small / medium / large-v3")),
            "device": _select(),
            "compute_type": _text(_("int8 / float16 / float32")),
            "beam_size": _number("5"),
            "language": _text(_("en, pl… or blank")),
            "is_active": _checkbox(),
        }


class SpeechModelDownloadForm(forms.Form):
    download_source = forms.CharField(
        label=_("Download source"),
        max_length=512,
        widget=_text(_("e.g. Systran/faster-whisper-small  or  small  or  https://…/model.pt")),
        help_text=_(
            "HuggingFace repo ID, a simple size name (tiny/base/small/medium/large-v3), "
            "or a direct HTTPS URL to a .pt file."
        ),
    )


class TranscriptionJobForm(forms.ModelForm):
    run_async = forms.BooleanField(label=_("Run in Celery (async)"), required=False, initial=True, widget=_checkbox())
    timeout_seconds = forms.IntegerField(
        label=_("Sync timeout (seconds)"),
        required=False,
        min_value=10,
        max_value=7200,
        widget=_number(_("e.g. 300")),
        help_text=_("Max seconds for synchronous runs. Leave blank for no limit."),
    )

    class Meta:
        model = TranscriptionJob
        fields = ["engine", "language", "detect_speakers", "translate_to", "prompt"]
        widgets = {
            "engine": _select(),
            "language": _text(_("Optional override, e.g. en")),
            "translate_to": _text(_("Optional target language")),
            "prompt": _textarea(3, _("Optional vocabulary, speaker names, domain hints…")),
            "detect_speakers": _checkbox(),
        }
