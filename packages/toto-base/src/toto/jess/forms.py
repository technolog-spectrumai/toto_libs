"""The compose form — the staff send page, and the thing that grows into a campaign."""
from django import forms

from .models import EmailProvider


class ComposeForm(forms.Form):
    """One message, typed by a human.

    Recipients are a comma-separated field rather than a formset because this is a send
    surface for the odd message by hand: the point is to get one out quickly and see
    what happened, not to manage an audience.

    ``manual=True`` adds an optional passphrase field: on a manual-release host the
    composer can type the vault passphrase to send their own message immediately, rather
    than leaving it held for the release page. Left blank, the message is simply held.
    """

    to = forms.CharField(
        label="To",
        help_text="One or more addresses, comma separated.",
        widget=forms.TextInput(attrs={"placeholder": "someone@example.org"}),
    )
    subject = forms.CharField(label="Subject", max_length=255)
    body = forms.CharField(
        label="Message", widget=forms.Textarea(attrs={"rows": 10}),
        help_text="Plain text. Always sent, and used by clients that cannot show HTML.",
    )
    html_body = forms.CharField(
        label="HTML (optional)", required=False,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text="Attached as an alternative part, not a replacement.",
    )
    passphrase = forms.CharField(
        label="Vault passphrase (to send now)", required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Manual-release host: type it to send this message immediately. "
                  "Leave blank to hold it for release later.",
    )

    def __init__(self, *args, manual: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        if not manual:
            # The passphrase field is meaningless on a non-manual host, so it is not
            # even rendered there — no ambient/typed distinction exists.
            del self.fields["passphrase"]

    def clean_to(self):
        raw = self.cleaned_data["to"]
        addresses = [a.strip() for a in raw.split(",") if a.strip()]
        if not addresses:
            raise forms.ValidationError("At least one recipient is required.")
        validate = forms.EmailField().clean
        for address in addresses:
            try:
                validate(address)
            except forms.ValidationError:
                raise forms.ValidationError(f"{address!r} is not a valid email address.")
        return addresses


class AccountForm(forms.ModelForm):
    """Set up the email account from the staff UI instead of the Django admin.

    A ModelForm so ``EmailProvider.clean()`` (SMTP needs a host; STARTTLS and implicit TLS
    are mutually exclusive) and the one-active-row invariant in ``save()`` both apply
    unchanged. The password is deliberately NOT a field here — it goes through the vault in
    the view, exactly as the admin does it, so the plaintext never rides on the form.
    """

    class Meta:
        model = EmailProvider
        fields = [
            "label", "backend", "host", "port", "use_tls", "use_ssl", "timeout",
            "username", "from_address", "reply_to", "active",
        ]
