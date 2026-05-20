from django import forms
from django.contrib.gis.geos import Point
from django.utils import timezone

from toto.locations.models import Address

from .models import Detection


class DetectionMapCreateForm(forms.ModelForm):
    latitude = forms.DecimalField(max_digits=9, decimal_places=6)
    longitude = forms.DecimalField(max_digits=9, decimal_places=6)
    location_label = forms.CharField(max_length=255, required=False)

    class Meta:
        model = Detection
        fields = [
            "title",
            "description",
            "category",
            "detection_type",
            "severity",
            "status",
            "start_time",
            "latitude",
            "longitude",
            "location_label",
        ]
        widgets = {
            "start_time": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "description": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, reporter=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.reporter = reporter
        if not self.is_bound:
            self.initial.setdefault("start_time", timezone.localtime().strftime("%Y-%m-%dT%H:%M"))
            self.initial.setdefault("status", "new")

    def save(self, commit=True):
        detection = super().save(commit=False)
        longitude = float(self.cleaned_data["longitude"])
        latitude = float(self.cleaned_data["latitude"])
        label = self.cleaned_data.get("location_label") or "Map detection"

        point = Point(longitude, latitude, srid=4326)
        address = Address.objects.create(
            country_name="",
            locality_name="",
            street=label,
            building="",
            geometry=point,
        )
        detection.address = address
        if self.reporter and not detection.reported_by_id:
            detection.reported_by = self.reporter
        if commit:
            detection.save()
            self.save_m2m()
        return detection
