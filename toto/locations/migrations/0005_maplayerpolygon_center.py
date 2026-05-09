# Generated manually on 2026-05-09

import django.contrib.gis.db.models.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("locations", "0004_maplayer_maplayerpolygon"),
    ]

    operations = [
        migrations.AddField(
            model_name="maplayerpolygon",
            name="center",
            field=django.contrib.gis.db.models.fields.PointField(
                blank=True,
                help_text="Stored center point used for layer labels and markers.",
                null=True,
                srid=4326,
            ),
        ),
    ]
