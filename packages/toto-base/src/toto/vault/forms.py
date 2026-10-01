from django import forms
from django.utils.translation import gettext_lazy as _

from toto.core.django_compat import URLFIELD_HTTPS

from .models import Bucket, VaultFile
from .peering import BucketPeer, decode_pairing_code, federated_host_choices


class CopyFilesForm(forms.Form):
    files = forms.ModelMultipleChoiceField(
        queryset=VaultFile.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        error_messages={"required": _("Select at least one file to copy.")},
    )
    destination_bucket = forms.ModelChoiceField(
        queryset=Bucket.objects.none(),
        required=True,
        empty_label=_("— select destination —"),
        error_messages={"required": _("Choose a destination bucket.")},
    )

    def __init__(self, user, source_bucket, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["files"].queryset = VaultFile.objects.filter(
            owner=user, bucket=source_bucket
        ).order_by("title")
        # A bucket being deleted takes nothing new (models.BucketClosed).
        self.fields["destination_bucket"].queryset = Bucket.objects.filter(
            owner=user, deletion_requested_at__isnull=True
        ).exclude(pk=source_bucket.pk).order_by("name")


class BucketPeerPairingForm(forms.ModelForm):
    """The add form: pick who you federated with, paste their pairing code.

    Moved out of admin.py so a non-admin door can render the same form. The
    ``clean()`` contract is load-bearing and unchanged: ``BucketPeerAdmin.save_model``
    reads ``cleaned_data["decoded_code"]`` and ``["resolved_base_url"]``.
    """

    paired_host = forms.ChoiceField(
        required=False, label=_("Paired host"),
        help_text=_("Hosts known from SSO federation. Pick one, or leave on "
                    "'Other host' and fill the URL below."))
    base_url = forms.URLField(
        required=False, label=_("Other host URL"),
        help_text=_("Only when the host is not in the list, "
                    "e.g. https://placidia.example.org"),
        **URLFIELD_HTTPS)
    pairing_code = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), label=_("Pairing code"),
        help_text=_("Minted once by a bucket grant on the exporting host."))

    class Meta:
        model = BucketPeer
        fields = ("label",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["paired_host"].choices = (
            [("", "Other host (enter URL below)")] + federated_host_choices())

    def clean(self):
        cleaned = super().clean()
        base_url = cleaned.get("paired_host") or cleaned.get("base_url", "")
        if not base_url:
            raise forms.ValidationError(
                "Pick a paired host or enter the host URL.")
        cleaned["resolved_base_url"] = base_url.rstrip("/")
        if cleaned.get("pairing_code"):
            cleaned["decoded_code"] = decode_pairing_code(
                cleaned["pairing_code"])
        return cleaned
