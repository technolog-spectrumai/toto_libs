from django import forms
from django.utils.text import slugify

from toto.vault.models import Bucket, VaultFile


class LibraryBibExportForm(forms.Form):
    bucket = forms.ModelChoiceField(
        queryset=Bucket.objects.none(),
        required=True,
        label="Vault bucket",
    )
    file_name = forms.CharField(
        max_length=255,
        required=True,
        label="File name",
        help_text="Example: my-reading-list.bib. The name is normalized into a Vault key.",
    )
    make_public = forms.BooleanField(
        required=False,
        initial=False,
        label="Make exported file public",
    )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        queryset = Bucket.objects.all().order_by("name")
        if user and not user.is_superuser:
            queryset = queryset.filter(owner=user)
        self.fields["bucket"].queryset = queryset

    def clean_file_name(self):
        value = self.cleaned_data["file_name"].strip()
        if value.lower().endswith(".bib"):
            value = value[:-4]
        key = slugify(value)
        if not key:
            raise forms.ValidationError("Enter a valid file name.")
        return key

    def clean(self):
        cleaned = super().clean()
        bucket = cleaned.get("bucket")
        key = cleaned.get("file_name")
        if bucket and key and VaultFile.objects.filter(bucket=bucket, key=key).exists():
            raise forms.ValidationError(
                f"A file named '{key}.bib' already exists in bucket '{bucket.name}'."
            )
        return cleaned
