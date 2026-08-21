from django import forms
from django.utils.translation import gettext_lazy as _

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
        self.fields["destination_bucket"].queryset = Bucket.objects.filter(
            owner=user
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
                    "e.g. https://placidia.example.org"))
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


class S3BucketForm(forms.Form):
    """Create an S3-compatible bucket: typed fields, a whitelisted config.

    No credential field of any kind. Credentials are either ambient (the boto3
    chain) or sealed under a storage PIN through the credential flow — never a
    column this form could write.
    """

    name = forms.CharField(max_length=100, label=_("Bucket name (here)"))
    provider = forms.ModelChoiceField(
        queryset=Bucket._meta.get_field("provider").related_model.objects.all(),
        required=False,
        empty_label=_("Custom endpoint (no preset)"),
        label=_("Provider preset"),
        help_text=_("A preset supplies the endpoint and addressing style."))
    bucket_name = forms.CharField(
        max_length=255, label=_("Bucket at the provider"),
        help_text=_("The bucket's name on the provider's side."))
    endpoint_url = forms.CharField(
        required=False, label=_("Endpoint URL"),
        help_text=_("Only for a custom endpoint. Left blank, the preset (or "
                    "AWS default routing) decides."))
    region_name = forms.CharField(required=False, max_length=64, label=_("Region"))
    prefix = forms.CharField(
        required=False, max_length=255, label=_("Key prefix"),
        help_text=_("A path prefix for every object key, e.g. vault/."))

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if Bucket.objects.filter(name=name).exists():
            raise forms.ValidationError(_("A bucket with that name already exists."))
        return name

    def clean_endpoint_url(self):
        """The same guard the driver applies, surfaced as a form sentence.

        Without this, a private endpoint would be accepted at save and refuse
        at first use — a delayed failure nobody can diagnose from the page.
        """
        raw = (self.cleaned_data.get("endpoint_url") or "").strip()
        if not raw:
            return ""
        from .outbound import OutboundRefused, assert_outbound_allowed

        try:
            return assert_outbound_allowed(raw, label=_("Endpoint URL"))
        except OutboundRefused as exc:
            raise forms.ValidationError(str(exc))

    def storage_config(self) -> dict:
        """The whitelist, and nothing else survives."""
        config = {"bucket_name": self.cleaned_data["bucket_name"].strip()}
        for key in ("endpoint_url", "region_name", "prefix"):
            value = (self.cleaned_data.get(key) or "").strip()
            if value:
                config[key] = value
        return config


class FederatedMountForm(forms.Form):
    """Mount a bucket from a FEDERATED platform. Only federated.

    Deliberately narrower than the admin's ``BucketPeerPairingForm``: there is
    no free-text URL here. The host list comes from the SSO federation rows,
    so a staff member can only point this server at a platform an operator has
    already paired with — the admin keeps the free-text door for the cases
    federation does not cover.
    """

    name = forms.CharField(max_length=100, label=_("Bucket name (here)"))
    paired_host = forms.ChoiceField(
        label=_("Federated platform"),
        help_text=_("Hosts known from SSO federation."))
    pairing_code = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), label=_("Pairing code"),
        help_text=_("Minted once by a bucket grant on the exporting host."))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["paired_host"].choices = federated_host_choices()

    @property
    def has_federated_hosts(self) -> bool:
        return bool(self.fields["paired_host"].choices)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if Bucket.objects.filter(name=name).exists():
            raise forms.ValidationError(_("A bucket with that name already exists."))
        return name

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("pairing_code"):
            cleaned["decoded_code"] = decode_pairing_code(cleaned["pairing_code"])
        return cleaned
