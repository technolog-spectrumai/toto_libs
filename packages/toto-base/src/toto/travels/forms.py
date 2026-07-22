from django import forms
from django.utils import timezone
from .models import Travel, Visit
from django.contrib.gis.geos import Point


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
        fields = ["route", "participants", "score", "info"]

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
    _dt_attrs = {
        "class": (
            "w-full rounded-lg border px-4 py-2 text-sm outline-none transition "
            "focus:ring-2 focus:ring-current/20"
        ),
        "x-bind:class": (
            "darkMode "
            "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
            ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
        ),
    }

    visited_date = forms.DateField(
        label="Arrival date",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", **_dt_attrs}),
    )

    visited_time = forms.TimeField(
        label="Arrival time",
        required=False,
        widget=forms.TimeInput(attrs={"type": "time", **_dt_attrs}),
    )

    ended_date = forms.DateField(
        label="Departure date",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", **_dt_attrs}),
    )

    ended_time = forms.TimeField(
        label="Departure time",
        required=False,
        widget=forms.TimeInput(attrs={"type": "time", **_dt_attrs}),
    )

    class Meta:
        model = Visit
        fields = ["participant", "location", "score", "review"]

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
        ended_date = cleaned_data.get("ended_date")
        ended_time = cleaned_data.get("ended_time")

        if visited_date and visited_time:
            cleaned_data["visited_at"] = timezone.make_aware(
                timezone.datetime.combine(visited_date, visited_time)
            )
        elif visited_date:
            cleaned_data["visited_at"] = timezone.make_aware(
                timezone.datetime.combine(visited_date, timezone.datetime.min.time())
            )
        else:
            cleaned_data["visited_at"] = None

        if ended_date and ended_time:
            cleaned_data["ends_at"] = timezone.make_aware(
                timezone.datetime.combine(ended_date, ended_time)
            )
        elif ended_date:
            cleaned_data["ends_at"] = timezone.make_aware(
                timezone.datetime.combine(ended_date, timezone.datetime.min.time())
            )
        else:
            cleaned_data["ends_at"] = None

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)

        instance.visited_at = self.cleaned_data.get("visited_at")
        instance.ends_at = self.cleaned_data.get("ends_at")

        if (instance.score or instance.review) and not instance.reviewed_at:
            instance.reviewed_at = timezone.now()

        if commit:
            instance.save()

        return instance
