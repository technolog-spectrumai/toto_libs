import uuid

from django.contrib.gis.db import models
from django.utils import timezone
from django.utils.text import slugify


class IncidentCategory(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Incident Category"
        verbose_name_plural = "Incident Categories"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Incident(models.Model):
    SEVERITY_CHOICES = [
        ("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical"),
    ]
    TYPE_CHOICES = [
        ("emergency", "Emergency"),
        ("hazard", "Hazard"),
        ("breach", "Breach"),
        ("infrastructure", "Infrastructure"),
        ("environmental", "Environmental"),
        ("other", "Other"),
    ]
    STATUS_CHOICES = [
        ("open", "Open"),
        ("contained", "Contained"),
        ("resolved", "Resolved"),
        ("closed", "Closed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    category = models.ForeignKey(
        IncidentCategory, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="incidents",
    )
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES, default="medium")
    incident_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default="other")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")

    # Location — address or zone (route-based detections are rare as confirmed incidents)
    address = models.ForeignKey(
        "locations.Address", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="incidents",
    )
    zone = models.ForeignKey(
        "locations.Zone", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="incidents",
    )
    # Raw geometry stored for imported incidents that don't match an existing address
    geometry = models.PointField(
        srid=4326, null=True, blank=True,
        help_text="Raw point for incidents imported without a matched address.",
    )

    # People
    reported_by = models.ForeignKey(
        "people.Person", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reported_incidents",
    )
    confirmed_by = models.ForeignKey(
        "auth.User", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="confirmed_incidents",
    )

    # Origin — set when promoted from a detection; null for directly imported incidents
    source_detection = models.OneToOneField(
        "detections.Detection", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="promoted_incident",
    )

    # Extra metadata preserved from the source (import properties, etc.)
    properties = models.JSONField(default=dict, blank=True)

    start_time = models.DateTimeField(default=timezone.now)
    end_time = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    public = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "severity"]),
            models.Index(fields=["incident_type"]),
        ]
        verbose_name = "Incident"
        verbose_name_plural = "Incidents"

    def __str__(self):
        return self.title

    @property
    def location_label(self):
        if self.address:
            return str(self.address)
        if self.zone:
            return self.zone.name
        return ""

    @property
    def map_geometry(self):
        if self.geometry:
            return self.geometry
        if self.address and self.address.geometry:
            return self.address.geometry
        if self.zone and self.zone.geometry:
            return self.zone.geometry
        return None

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("incidents:incident-detail", kwargs={"pk": self.pk})
