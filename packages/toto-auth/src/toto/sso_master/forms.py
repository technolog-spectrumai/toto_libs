from django import forms


class FederationBrandingForm(forms.ModelForm):
    """The federation's public face, edited from the Branding tab."""

    class Meta:
        from toto.core.models import Federation

        model = Federation
        fields = ("name", "description", "logo")
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}
