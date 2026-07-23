from django import forms
from .models import Address


class AddressCreateForm(forms.ModelForm):
    latitude = forms.FloatField(
        required=False,
        min_value=-90,
        max_value=90,
        widget=forms.NumberInput(attrs={
            "step": "any",
            "placeholder": "Latitude",
            "class": (
                "w-full rounded-lg border px-3 py-2 text-sm outline-none "
                "transition focus:ring-2 focus:ring-current/20"
            ),
            "x-bind:class": (
                "darkMode "
                "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
                ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
            ),
        }),
    )

    longitude = forms.FloatField(
        required=False,
        min_value=-180,
        max_value=180,
        widget=forms.NumberInput(attrs={
            "step": "any",
            "placeholder": "Longitude",
            "class": (
                "w-full rounded-lg border px-3 py-2 text-sm outline-none "
                "transition focus:ring-2 focus:ring-current/20"
            ),
            "x-bind:class": (
                "darkMode "
                "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
                ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
            ),
        }),
    )

    class Meta:
        model = Address
        fields = [
            "country_name",
            "state_or_province_name",
            "locality_name",
            "street",
            "building",
            "apartment",
        ]

        widgets = {
            "country_name": forms.TextInput(attrs={
                "placeholder": "PL",
                "maxlength": "2",
                "class": "w-full rounded-lg border px-3 py-2 text-sm uppercase outline-none transition focus:ring-2 focus:ring-current/20",
                "x-bind:class": "darkMode ? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' : 'border-accent-2 bg-primary-bg-light text-text-main-light'",
            }),
            "state_or_province_name": forms.TextInput(attrs={
                "placeholder": "Region / state / province",
                "class": "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20",
                "x-bind:class": "darkMode ? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' : 'border-accent-2 bg-primary-bg-light text-text-main-light'",
            }),
            "locality_name": forms.TextInput(attrs={
                "placeholder": "City / town / village",
                "class": "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20",
                "x-bind:class": "darkMode ? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' : 'border-accent-2 bg-primary-bg-light text-text-main-light'",
            }),
            "street": forms.TextInput(attrs={
                "placeholder": "Street, place, landmark, road, or area",
                "class": "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20",
                "x-bind:class": "darkMode ? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' : 'border-accent-2 bg-primary-bg-light text-text-main-light'",
            }),
            "building": forms.TextInput(attrs={
                "placeholder": "Building number / landmark",
                "class": "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20",
                "x-bind:class": "darkMode ? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' : 'border-accent-2 bg-primary-bg-light text-text-main-light'",
            }),
            "apartment": forms.TextInput(attrs={
                "placeholder": "Optional",
                "class": "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20",
                "x-bind:class": "darkMode ? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' : 'border-accent-2 bg-primary-bg-light text-text-main-light'",
            }),
        }

    def __init__(self, *args, **kwargs):
        latitude = kwargs.pop("latitude", None)
        longitude = kwargs.pop("longitude", None)

        super().__init__(*args, **kwargs)

        for field_name in [
            "country_name",
            "state_or_province_name",
            "locality_name",
            "street",
            "building",
            "apartment",
        ]:
            self.fields[field_name].required = False

        if latitude not in (None, ""):
            self.fields["latitude"].initial = latitude

        if longitude not in (None, ""):
            self.fields["longitude"].initial = longitude

    def clean_country_name(self):
        value = (self.cleaned_data.get("country_name") or "").strip().upper()

        if value and len(value) != 2:
            raise forms.ValidationError(
                "Use a 2-letter country code, for example PL, FR, or GB."
            )

        return value

    def save(self, commit=True):
        address = super().save(commit=False)

        latitude = self.cleaned_data.get("latitude")
        longitude = self.cleaned_data.get("longitude")

        if latitude is not None and longitude is not None:
            # Address stores canonical lat/lon; Address.save() derives geometry
            # from them on a GIS build. Keeps this form GDAL-free.
            address.latitude = latitude
            address.longitude = longitude

        if commit:
            address.save()

        return address
