from django import forms

from .models import ScheduledEvent

FIELD_CLASS = (
    "w-full rounded-lg border px-3 py-2 text-sm outline-none "
    "shadow-inner transition focus:ring-2 focus:ring-current/20"
)

FIELD_THEME_CLASS = (
    "darkMode "
    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark placeholder:text-text-main-dark/45' "
    ": 'border-accent-2 bg-primary-bg-light text-text-main-light placeholder:text-text-main-light/45'"
)

CHECKBOX_CLASS = "h-4 w-4 rounded"

CHECKBOX_THEME_CLASS = (
    "darkMode "
    "? 'border-accent-1 bg-primary-bg-dark' "
    ": 'border-accent-2 bg-primary-bg-light'"
)

SPLIT_FIELDS = {"start_time", "end_time"}


class ScheduledEventForm(forms.ModelForm):
    start_time = forms.SplitDateTimeField(
        widget=forms.SplitDateTimeWidget(
            date_attrs={"type": "date"},
            time_attrs={"type": "time"},
            date_format="%Y-%m-%d",
            time_format="%H:%M",
        )
    )
    end_time = forms.SplitDateTimeField(
        widget=forms.SplitDateTimeWidget(
            date_attrs={"type": "date"},
            time_attrs={"type": "time"},
            date_format="%Y-%m-%d",
            time_format="%H:%M",
        )
    )

    class Meta:
        model = ScheduledEvent
        fields = [
            "title",
            "description",
            "start_time",
            "end_time",
            "category",
            "address",
            "capacity",
            "requires_registration",
            "public",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Event title"}),
            "description": forms.Textarea(attrs={"rows": 5, "placeholder": "Describe the event..."}),
            "category": forms.Select(),
            "address": forms.Select(),
            "capacity": forms.NumberInput(attrs={"placeholder": "Leave blank for unlimited"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name in SPLIT_FIELDS:
                for w in field.widget.widgets:
                    w.attrs.setdefault("class", FIELD_CLASS)
                    w.attrs.setdefault("x-bind:class", FIELD_THEME_CLASS)
            elif name in ("public", "requires_registration"):
                field.widget.attrs.setdefault("class", CHECKBOX_CLASS)
                field.widget.attrs.setdefault("x-bind:class", CHECKBOX_THEME_CLASS)
            else:
                field.widget.attrs.setdefault("class", FIELD_CLASS)
                field.widget.attrs.setdefault("x-bind:class", FIELD_THEME_CLASS)
