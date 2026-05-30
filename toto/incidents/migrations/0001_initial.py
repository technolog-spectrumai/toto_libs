import uuid
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.contrib.gis.db import models
from django.db import migrations


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("detections", "0002_initial"),
        ("locations", "0001_initial"),
        ("people", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="IncidentCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("slug", models.SlugField(blank=True, max_length=120, unique=True)),
                ("description", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"ordering": ["name"], "verbose_name": "Incident Category", "verbose_name_plural": "Incident Categories"},
        ),
        migrations.CreateModel(
            name="Incident",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=300)),
                ("description", models.TextField(blank=True)),
                ("severity", models.CharField(choices=[("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")], default="medium", max_length=16)),
                ("incident_type", models.CharField(choices=[("emergency", "Emergency"), ("hazard", "Hazard"), ("breach", "Breach"), ("infrastructure", "Infrastructure"), ("environmental", "Environmental"), ("other", "Other")], default="other", max_length=30)),
                ("status", models.CharField(choices=[("open", "Open"), ("contained", "Contained"), ("resolved", "Resolved"), ("closed", "Closed")], default="open", max_length=20)),
                ("geometry", models.PointField(blank=True, help_text="Raw point for incidents imported without a matched address.", null=True, srid=4326)),
                ("properties", models.JSONField(blank=True, default=dict)),
                ("start_time", models.DateTimeField(default=django.utils.timezone.now)),
                ("end_time", models.DateTimeField(blank=True, null=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("public", models.BooleanField(default=False)),
                ("address", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="incidents", to="locations.address")),
                ("zone", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="incidents", to="locations.zone")),
                ("category", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="incidents", to="incidents.incidentcategory")),
                ("reported_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reported_incidents", to="people.person")),
                ("confirmed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="confirmed_incidents", to=settings.AUTH_USER_MODEL)),
                ("source_detection", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="promoted_incident", to="detections.detection")),
            ],
            options={"ordering": ["-created_at"], "verbose_name": "Incident", "verbose_name_plural": "Incidents"},
        ),
        migrations.AddIndex(
            model_name="incident",
            index=models.Index(fields=["status", "severity"], name="incidents_i_status_sev_idx"),
        ),
        migrations.AddIndex(
            model_name="incident",
            index=models.Index(fields=["incident_type"], name="incidents_i_type_idx"),
        ),
    ]
