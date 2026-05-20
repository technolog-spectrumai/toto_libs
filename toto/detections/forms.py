from django import forms
from django.contrib.gis.geos import Point
from django.utils import timezone

from toto.kanban.models import Mission
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


class DetectionHelpForm(forms.Form):
    MODE_TASK = "task"
    MODE_SERVICE = "service"
    MODE_CHOICES = [
        (MODE_TASK, "Create a Kanban task"),
        (MODE_SERVICE, "Outsource as a Bazaar service"),
    ]

    mode = forms.ChoiceField(choices=MODE_CHOICES, widget=forms.RadioSelect)
    mission = forms.ModelChoiceField(
        queryset=Mission.objects.none(),
        required=False,
        help_text="Mission that should receive the task.",
    )
    title = forms.CharField(max_length=255)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 5}), required=False)
    service_price = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0, required=False)
    service_currency = forms.CharField(max_length=3, initial="PLN", required=False)

    def __init__(self, *args, detection=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.detection = detection
        self.fields["mission"].queryset = Mission.objects.select_related(
            "campaign",
            "campaign__project",
        ).order_by("campaign__project__name", "campaign__name", "title")
        if detection and not self.is_bound:
            self.initial.setdefault("mode", self.MODE_TASK)
            self.initial.setdefault("mission", detection.mitigation_task.mission_id if detection.mitigation_task_id else None)
            self.initial.setdefault("title", f"Help with: {detection.title}")
            self.initial.setdefault("description", detection.description)

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get("mode")
        if mode == self.MODE_TASK and not cleaned.get("mission"):
            self.add_error("mission", "Choose a mission for the Kanban task.")
        if mode == self.MODE_SERVICE and cleaned.get("service_price") is None:
            self.add_error("service_price", "Set a service price for Bazaar outsourcing.")
        return cleaned
