from django import forms
from django.utils.translation import gettext_lazy as _

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
            "title": forms.TextInput(attrs={"placeholder": _("Event title")}),
            "description": forms.Textarea(attrs={"rows": 5, "placeholder": _("Describe the event...")}),
            "category": forms.Select(),
            "address": forms.TextInput(attrs={"placeholder": _("Where it takes place")}),
            "capacity": forms.NumberInput(attrs={"placeholder": _("Leave blank for unlimited")}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # The place is text the organiser types (2026-10-04); it was a list
        # of the map addresses the creator may read. On a host with the map
        # (toto.locations) that list is back beside the text (2026-10-12),
        # for an organiser who has the map: "Place on the map", the
        # registered places they may read, kept as the map's own row
        # (locations.EventPlace) by ``save_place``.
        self.has_place = False
        from django.apps import apps

        if apps.is_installed("toto.locations"):
            from toto.locations import links

            if links.map_open_to(user):
                self.fields["place"] = links.place_field(user, self.instance)
                self.has_place = True
                names = list(self.fields)
                names.remove("place")
                names.insert(names.index("address") + 1, "place")
                self.order_fields(names)
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

    def clean(self):
        cleaned = super().clean()
        # A place chosen from the map names the event's place where the
        # organiser typed none: every page that shows the text shows it.
        place = cleaned.get("place") if self.has_place else None
        if place is not None and not (cleaned.get("address") or "").strip():
            cleaned["address"] = str(place)[:255]
            self.instance.address = cleaned["address"]
        return cleaned

    def save_place(self, event) -> None:
        """Tie the saved event to the place chosen from the map, or untie
        it. Nothing on a host without the map, or for an organiser without
        it: their form had no such list."""
        if not self.has_place:
            return
        from toto.locations import links

        links.set_event_place(event, self.cleaned_data.get("place"))

