# GIS-off variant — identical to the GIS-on 0005. On a GIS-off build these
# lat/lon floats are Address's only coordinate store.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0004_address_note_route_notes'),
    ]

    operations = [
        migrations.AddField(
            model_name='address',
            name='latitude',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='address',
            name='longitude',
            field=models.FloatField(blank=True, null=True),
        ),
    ]
