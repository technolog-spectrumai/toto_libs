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
    width = forms.IntegerField(
        initial=1280,
        widget=forms.NumberInput(attrs=_w({"placeholder": "1280"})),
    )
    height = forms.IntegerField(
        initial=-2,
        help_text="Use -2 to preserve aspect ratio.",
        widget=forms.NumberInput(attrs=_w({"placeholder": "-2"})),
    )
    output_name = forms.CharField(
        initial="resized",
        widget=forms.TextInput(attrs=_w({"placeholder": "resized"})),
    )


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


class ConcatForm(forms.Form):
    input_file_ids = forms.CharField(
        label="VaultFile IDs (comma-separated)",
        widget=forms.TextInput(attrs=_w({"placeholder": "1, 2, 3"})),
        help_text="IDs of VaultFile records to concatenate, in order.",
    )
    reencode = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "rounded border", "x-bind:class": _XBIND}),
        help_text="Re-encode (slower but handles incompatible streams).",
    )
    output_name = forms.CharField(
        initial="merged",
        widget=forms.TextInput(attrs=_w({"placeholder": "merged"})),
    )

    def clean_input_file_ids(self):
        raw = self.cleaned_data["input_file_ids"]
        try:
            return [int(x.strip()) for x in raw.split(",") if x.strip()]
        except ValueError:
            raise forms.ValidationError("Enter comma-separated integer IDs.")
