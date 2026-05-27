from django import forms
from .models import Bucket, VaultFile


class CopyFilesForm(forms.Form):
    files = forms.ModelMultipleChoiceField(
        queryset=VaultFile.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        error_messages={"required": "Select at least one file to copy."},
    )
    destination_bucket = forms.ModelChoiceField(
        queryset=Bucket.objects.none(),
        required=True,
        empty_label="— select destination —",
        error_messages={"required": "Choose a destination bucket."},
    )

    def __init__(self, user, source_bucket, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["files"].queryset = VaultFile.objects.filter(
            owner=user, bucket=source_bucket
        ).order_by("title")
        self.fields["destination_bucket"].queryset = Bucket.objects.filter(
            owner=user
        ).exclude(pk=source_bucket.pk).order_by("name")
