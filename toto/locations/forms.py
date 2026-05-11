from django import forms
from django.utils import timezone
from .models import Travel, Visit, Address


class TravelForm(forms.ModelForm):
    start_date = forms.DateField(
        label="Start date",
        widget=forms.DateInput(attrs={
            "type": "date",
            "class": (
                "w-full rounded-lg border px-4 py-2 text-sm outline-none transition "
                "focus:ring-2 focus:ring-current/20"
            ),
            "x-bind:class": (
                "darkMode "
                "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
                ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
            ),
        }),
    )

    start_time = forms.TimeField(
        label="Start time",
        widget=forms.TimeInput(attrs={
            "type": "time",
            "class": (
                "w-full rounded-lg border px-4 py-2 text-sm outline-none transition "
                "focus:ring-2 focus:ring-current/20"
            ),
            "x-bind:class": (
                "darkMode "
                "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
                ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
            ),
        }),
    )

    end_date = forms.DateField(
        label="End date",
        widget=forms.DateInput(attrs={
            "type": "date",
            "class": (
                "w-full rounded-lg border px-4 py-2 text-sm outline-none transition "
                "focus:ring-2 focus:ring-current/20"
            ),
            "x-bind:class": (
                "darkMode "
                "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
                ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
            ),
        }),
    )

    end_time = forms.TimeField(
        label="End time",
        widget=forms.TimeInput(attrs={
            "type": "time",
            "class": (
                "w-full rounded-lg border px-4 py-2 text-sm outline-none transition "
                "focus:ring-2 focus:ring-current/20"
            ),
            "x-bind:class": (
                "darkMode "
                "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
                ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
            ),
        }),
    )

    class Meta:
        model = Travel
        fields = [
            "route",
            "participants",
            "score",
            "info",
        ]

        widgets = {
            "route": forms.Select(attrs={
                "class": (
                    "w-full rounded-lg border px-4 py-2 text-sm outline-none transition "
                    "focus:ring-2 focus:ring-current/20"
                ),
                "x-bind:class": (
                    "darkMode "
                    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
                    ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
                ),
            }),
            "participants": forms.SelectMultiple(attrs={
                "class": (
                    "w-full min-h-32 rounded-lg border px-4 py-2 text-sm outline-none transition "
                    "focus:ring-2 focus:ring-current/20"
                ),
                "x-bind:class": (
                    "darkMode "
                    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
                    ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
                ),
            }),
            "score": forms.NumberInput(attrs={
                "min": 1,
                "max": 5,
                "placeholder": "1–5",
                "class": (
                    "w-full rounded-lg border px-4 py-2 text-sm outline-none transition "
                    "focus:ring-2 focus:ring-current/20"
                ),
                "x-bind:class": (
                    "darkMode "
                    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
                    ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
                ),
            }),
            "info": forms.Textarea(attrs={
                "rows": 5,
                "placeholder": "Describe the travel, experience, route quality, notes, or review...",
                "class": (
                    "w-full rounded-lg border px-4 py-3 text-sm outline-none transition "
                    "focus:ring-2 focus:ring-current/20"
                ),
                "x-bind:class": (
                    "darkMode "
                    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
                    ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
                ),
            }),
        }

        labels = {
            "route": "Route",
            "participants": "Participants",
            "score": "Score",
            "info": "Travel notes",
        }

    def clean(self):
        cleaned_data = super().clean()

        start_date = cleaned_data.get("start_date")
        start_time = cleaned_data.get("start_time")
        end_date = cleaned_data.get("end_date")
        end_time = cleaned_data.get("end_time")

        if start_date and start_time:
            cleaned_data["starts_at"] = timezone.make_aware(
                timezone.datetime.combine(start_date, start_time)
            )

        if end_date and end_time:
            cleaned_data["ends_at"] = timezone.make_aware(
                timezone.datetime.combine(end_date, end_time)
            )

        starts_at = cleaned_data.get("starts_at")
        ends_at = cleaned_data.get("ends_at")

        if starts_at and ends_at and ends_at <= starts_at:
            raise forms.ValidationError("End time must be after start time.")

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)

        instance.starts_at = self.cleaned_data["starts_at"]
        instance.ends_at = self.cleaned_data["ends_at"]

        if instance.score and not instance.reviewed_at:
            instance.reviewed_at = timezone.now()

        if commit:
            instance.save()
            self.save_m2m()

        return instance


class VisitForm(forms.ModelForm):
    visited_date = forms.DateField(
        label="Visit date",
        required=False,
        widget=forms.DateInput(attrs={
            "type": "date",
            "class": (
                "w-full rounded-lg border px-4 py-2 text-sm outline-none transition "
                "focus:ring-2 focus:ring-current/20"
            ),
            "x-bind:class": (
                "darkMode "
                "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
                ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
            ),
        }),
    )

    visited_time = forms.TimeField(
        label="Visit time",
        required=False,
        widget=forms.TimeInput(attrs={
            "type": "time",
            "class": (
                "w-full rounded-lg border px-4 py-2 text-sm outline-none transition "
                "focus:ring-2 focus:ring-current/20"
            ),
            "x-bind:class": (
                "darkMode "
                "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
                ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
            ),
        }),
    )

    class Meta:
        model = Visit
        fields = [
            "participant",
            "location",
            "score",
            "review",
        ]

        widgets = {
            "participant": forms.Select(attrs={
                "class": (
                    "w-full rounded-lg border px-4 py-2 text-sm outline-none transition "
                    "focus:ring-2 focus:ring-current/20"
                ),
                "x-bind:class": (
                    "darkMode "
                    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
                    ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
                ),
            }),
            "location": forms.Select(attrs={
                "class": (
                    "w-full rounded-lg border px-4 py-2 text-sm outline-none transition "
                    "focus:ring-2 focus:ring-current/20"
                ),
                "x-bind:class": (
                    "darkMode "
                    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
                    ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
                ),
            }),
            "score": forms.NumberInput(attrs={
                "min": 1,
                "max": 5,
                "placeholder": "1–5",
                "class": (
                    "w-full rounded-lg border px-4 py-2 text-sm outline-none transition "
                    "focus:ring-2 focus:ring-current/20"
                ),
                "x-bind:class": (
                    "darkMode "
                    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
                    ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
                ),
            }),
            "review": forms.Textarea(attrs={
                "rows": 5,
                "placeholder": "Write a short visit note or review...",
                "class": (
                    "w-full rounded-lg border px-4 py-3 text-sm outline-none transition "
                    "focus:ring-2 focus:ring-current/20"
                ),
                "x-bind:class": (
                    "darkMode "
                    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
                    ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
                ),
            }),
        }

        labels = {
            "participant": "Visitor",
            "location": "Location",
            "score": "Score",
            "review": "Review",
        }

    def clean(self):
        cleaned_data = super().clean()

        visited_date = cleaned_data.get("visited_date")
        visited_time = cleaned_data.get("visited_time")

        if visited_date and visited_time:
            cleaned_data["visited_at"] = timezone.make_aware(
                timezone.datetime.combine(visited_date, visited_time)
            )
        elif visited_date and not visited_time:
            cleaned_data["visited_at"] = timezone.make_aware(
                timezone.datetime.combine(visited_date, timezone.datetime.min.time())
            )
        else:
            cleaned_data["visited_at"] = None

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)

        instance.visited_at = self.cleaned_data.get("visited_at")

        if (instance.score or instance.review) and not instance.reviewed_at:
            instance.reviewed_at = timezone.now()

        if commit:
            instance.save()

        return instance


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
                "placeholder": "Pomeranian",
                "class": "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20",
                "x-bind:class": "darkMode ? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' : 'border-accent-2 bg-primary-bg-light text-text-main-light'",
            }),
            "locality_name": forms.TextInput(attrs={
                "placeholder": "Gdańsk",
                "class": "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20",
                "x-bind:class": "darkMode ? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' : 'border-accent-2 bg-primary-bg-light text-text-main-light'",
            }),
            "street": forms.TextInput(attrs={
                "placeholder": "Long Market",
                "class": "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20",
                "x-bind:class": "darkMode ? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' : 'border-accent-2 bg-primary-bg-light text-text-main-light'",
            }),
            "building": forms.TextInput(attrs={
                "placeholder": "Landmark / building number",
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

        if latitude is not None:
            self.fields["latitude"].initial = latitude

        if longitude is not None:
            self.fields["longitude"].initial = longitude

    def clean_country_name(self):
        value = self.cleaned_data["country_name"].strip().upper()
        if len(value) != 2:
            raise forms.ValidationError("Use a 2-letter country code, for example PL, FR, or GB.")
        return value

    def save(self, commit=True):
        address = super().save(commit=False)

        latitude = self.cleaned_data.get("latitude")
        longitude = self.cleaned_data.get("longitude")

        if latitude is not None and longitude is not None:
            address.geometry = Point(longitude, latitude, srid=4326)

        if commit:
            address.save()

        return address