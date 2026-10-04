# GIS-off variant of locations/migrations/0002_initial.py. Keep it in lockstep with the
# GIS-on graph (2026-10-01 reset): only geometry fields may differ, and this one has none.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('people', '0001_initial'),
        ('locations', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='maplayer',
            name='owner',
            field=models.ForeignKey(blank=True, help_text='Person who owns or manages this map layer', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='owned_map_layers', to='people.person'),
        ),
    ]
