"""The compose form — the staff send page, and the thing that grows into a campaign."""
from django import forms


class ComposeForm(forms.Form):
    """One message, typed by a human.

    Recipients are a comma-separated field rather than a formset because this is a test
    and diagnostic surface: the point is to get a message out quickly and see what
    happened, not to manage an audience.
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
