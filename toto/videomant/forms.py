from django import forms

_INPUT_CLASS = (
    "w-full rounded-lg border px-3 py-2 text-sm "
    "focus:outline-none focus:ring-2 focus:ring-offset-1"
)
_XBIND = (
    "darkMode "
    "? 'bg-primary-bg-dark border-accent-1 text-text-main-dark focus:ring-accent-1' "
    ": 'bg-primary-bg-light border-accent-2 text-text-main-light focus:ring-accent-2'"
)


def _w(extra_attrs: dict | None = None):
    attrs = {"class": _INPUT_CLASS, "x-bind:class": _XBIND}
    if extra_attrs:
        attrs.update(extra_attrs)
    return attrs


class CompressForm(forms.Form):
    QUALITY_CHOICES = [
        ("tiny",    "Tiny (smallest)"),
        ("small",   "Small"),
        ("medium",  "Medium (default)"),
        ("high",    "High"),
        ("archive", "Archive (best)"),
    ]
    quality = forms.ChoiceField(
        choices=QUALITY_CHOICES,
        initial="medium",
        widget=forms.Select(attrs=_w()),
    )
    output_name = forms.CharField(
        initial="compressed",
        widget=forms.TextInput(attrs=_w({"placeholder": "compressed"})),
    )


class ResizeForm(forms.Form):
    preserve_aspect_ratio = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={
            "class": "rounded border",
            "x-bind:class": _XBIND,
            "x-model": "preserve",
        }),
        help_text="Provide only one dimension; the other is computed automatically.",
    )
    width = forms.IntegerField(
        required=False,
        initial=1280,
        widget=forms.NumberInput(attrs=_w({"placeholder": "1280", "x-bind:required": "!preserve"})),
    )
    height = forms.IntegerField(
        required=False,
        widget=forms.NumberInput(attrs=_w({"placeholder": "720", "x-bind:required": "!preserve"})),
    )
    output_name = forms.CharField(
        initial="resized",
        widget=forms.TextInput(attrs=_w({"placeholder": "resized"})),
    )

    def clean(self):
        data = super().clean()
        preserve = data.get("preserve_aspect_ratio", False)
        width = data.get("width")
        height = data.get("height")

        if preserve:
            if width and height:
                raise forms.ValidationError(
                    "Provide only one dimension (width or height) when preserving aspect ratio."
                )
            if not width and not height:
                raise forms.ValidationError(
                    "Provide either width or height when preserving aspect ratio."
                )
            # Fill the unconstrained dimension with -2 (ffmpeg auto-scale)
            data["width"] = width or -2
            data["height"] = height or -2
        else:
            if not width:
                self.add_error("width", "Width is required when not preserving aspect ratio.")
            if not height:
                self.add_error("height", "Height is required when not preserving aspect ratio.")

        return data


class CutForm(forms.Form):
    start_time = forms.CharField(
        initial="00:00:00",
        widget=forms.TextInput(attrs=_w({"placeholder": "00:00:10"})),
    )
    end_time = forms.CharField(
        initial="00:00:30",
        widget=forms.TextInput(attrs=_w({"placeholder": "00:00:30"})),
    )
    output_name = forms.CharField(
        initial="clip",
        widget=forms.TextInput(attrs=_w({"placeholder": "clip"})),
    )


class ExtractMp3Form(forms.Form):
    BITRATE_CHOICES = [
        ("128k", "128k"),
        ("192k", "192k (default)"),
        ("256k", "256k"),
        ("320k", "320k"),
    ]
    bitrate = forms.ChoiceField(
        choices=BITRATE_CHOICES,
        initial="192k",
        widget=forms.Select(attrs=_w()),
    )
    output_name = forms.CharField(
        initial="audio",
        widget=forms.TextInput(attrs=_w({"placeholder": "audio"})),
    )


class ThumbnailForm(forms.Form):
    at_time = forms.CharField(
        initial="00:00:01",
        widget=forms.TextInput(attrs=_w({"placeholder": "00:00:01"})),
    )
    output_name = forms.CharField(
        initial="thumbnail",
        widget=forms.TextInput(attrs=_w({"placeholder": "thumbnail"})),
    )


class GifForm(forms.Form):
    start_time = forms.CharField(
        initial="00:00:00",
        widget=forms.TextInput(attrs=_w({"placeholder": "00:00:00"})),
    )
    duration = forms.IntegerField(
        initial=5,
        widget=forms.NumberInput(attrs=_w({"placeholder": "5"})),
    )
    fps = forms.IntegerField(
        initial=12,
        widget=forms.NumberInput(attrs=_w({"placeholder": "12"})),
    )
    width = forms.IntegerField(
        initial=480,
        widget=forms.NumberInput(attrs=_w({"placeholder": "480"})),
    )
    output_name = forms.CharField(
        initial="animation",
        widget=forms.TextInput(attrs=_w({"placeholder": "animation"})),
    )


class CropForm(forms.Form):
    width = forms.IntegerField(
        initial=640,
        widget=forms.NumberInput(attrs=_w({"placeholder": "640"})),
    )
    height = forms.IntegerField(
        initial=480,
        widget=forms.NumberInput(attrs=_w({"placeholder": "480"})),
    )
    x = forms.IntegerField(
        initial=0, required=False,
        widget=forms.NumberInput(attrs=_w({"placeholder": "0"})),
    )
    y = forms.IntegerField(
        initial=0, required=False,
        widget=forms.NumberInput(attrs=_w({"placeholder": "0"})),
    )
    output_name = forms.CharField(
        initial="cropped",
        widget=forms.TextInput(attrs=_w({"placeholder": "cropped"})),
    )

    def clean(self):
        data = super().clean()
        data["x"] = data.get("x") or 0
        data["y"] = data.get("y") or 0
        return data


class ChangeFpsForm(forms.Form):
    fps = forms.IntegerField(
        initial=30,
        widget=forms.NumberInput(attrs=_w({"placeholder": "30"})),
    )
    output_name = forms.CharField(
        initial="fps",
        widget=forms.TextInput(attrs=_w({"placeholder": "fps"})),
    )


class RemoveAudioForm(forms.Form):
    output_name = forms.CharField(
        initial="muted",
        widget=forms.TextInput(attrs=_w({"placeholder": "muted"})),
    )


class ReplaceAudioForm(forms.Form):
    output_name = forms.CharField(
        initial="dubbed",
        widget=forms.TextInput(attrs=_w({"placeholder": "dubbed"})),
    )


class AddSubtitlesForm(forms.Form):
    output_name = forms.CharField(
        initial="subtitled",
        widget=forms.TextInput(attrs=_w({"placeholder": "subtitled"})),
    )


class AddWatermarkForm(forms.Form):
    POSITION_CHOICES = [
        ("top-left", "Top left"),
        ("top-right", "Top right"),
        ("bottom-left", "Bottom left"),
        ("bottom-right", "Bottom right (default)"),
        ("center", "Center"),
    ]
    position = forms.ChoiceField(
        choices=POSITION_CHOICES,
        initial="bottom-right",
        widget=forms.Select(attrs=_w()),
    )
    output_name = forms.CharField(
        initial="watermarked",
        widget=forms.TextInput(attrs=_w({"placeholder": "watermarked"})),
    )


class VstackForm(forms.Form):
    output_name = forms.CharField(
        initial="vstack",
        widget=forms.TextInput(attrs=_w({"placeholder": "vstack"})),
    )


class HstackForm(forms.Form):
    output_name = forms.CharField(
        initial="hstack",
        widget=forms.TextInput(attrs=_w({"placeholder": "hstack"})),
    )


class ConcatForm(forms.Form):
    """
    When bucket_files is provided (workspace context), renders a multi-select
    of real files. Otherwise falls back to a comma-separated ID text field.
    """

    reencode = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "rounded border", "x-bind:class": _XBIND}),
        help_text="Re-encode (slower, but handles incompatible streams).",
    )
    output_name = forms.CharField(
        initial="merged",
        widget=forms.TextInput(attrs=_w({"placeholder": "merged"})),
    )

    def __init__(self, *args, bucket_files=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._has_bucket = bool(bucket_files)
        if bucket_files:
            choices = [(vf.id, f"{vf.title} ({vf.file_type})") for vf in bucket_files]
            self.fields["extra_file_ids"] = forms.MultipleChoiceField(
                label="Add files from bucket",
                choices=choices,
                required=False,
                widget=forms.SelectMultiple(attrs={
                    "class": (
                        "w-full rounded-lg border px-3 py-2 text-sm focus:outline-none "
                        "min-h-[120px]"
                    ),
                    "x-bind:class": _XBIND,
                }),
                help_text="Hold Ctrl / ⌘ to select multiple. Files are appended after the primary file in order.",
            )
        else:
            self.fields["extra_file_ids"] = forms.CharField(
                label="Additional VaultFile IDs (comma-separated)",
                required=False,
                widget=forms.TextInput(attrs=_w({"placeholder": "2, 3, 4"})),
                help_text="IDs to append after the primary file.",
            )

    def clean_extra_file_ids(self):
        raw = self.cleaned_data.get("extra_file_ids") or []
        if self._has_bucket:
            try:
                return [int(x) for x in raw]
            except (ValueError, TypeError):
                raise forms.ValidationError("Invalid file selection.")
        if isinstance(raw, str):
            if not raw.strip():
                return []
            try:
                return [int(x.strip()) for x in raw.split(",") if x.strip()]
            except ValueError:
                raise forms.ValidationError("Enter comma-separated integer IDs.")
        return []
