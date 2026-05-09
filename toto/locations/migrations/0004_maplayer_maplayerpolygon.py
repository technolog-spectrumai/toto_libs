# Generated manually on 2026-05-09

import django.contrib.gis.db.models.fields
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("locations", "0003_routechain_route_route_chain_route_sequence"),
    ]

    operations = [
        migrations.CreateModel(
            name="MapLayer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("name", models.CharField(max_length=200)),
                ("slug", models.SlugField(max_length=220, unique=True)),
                ("description", models.TextField(blank=True)),
                ("unit", models.CharField(blank=True, help_text="Examples: °C, %, mm, ppm, people/km²", max_length=32)),
                (
                    "style",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Frontend style config: color scale, opacity, legend, etc.",
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="MapLayerPolygon",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("name", models.CharField(blank=True, max_length=200)),
                (
                    "geometry",
                    django.contrib.gis.db.models.fields.PolygonField(
                        help_text="Must be one continuous polygon.",
                        srid=4326,
                    ),
                ),
                ("value", models.FloatField()),
                (
                    "properties",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Optional metadata for frontend/domain use.",
                    ),
                ),
                (
                    "layer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="polygons",
                        to="locations.maplayer",
                    ),
                ),
            ],
            options={
                "abstract": False,
                "indexes": [models.Index(fields=["layer"], name="locations_m_layer__7c60f5_idx")],
            },
        ),
    ]
