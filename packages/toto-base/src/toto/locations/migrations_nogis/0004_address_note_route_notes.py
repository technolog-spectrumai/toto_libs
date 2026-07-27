# GIS-off variant — identical to the GIS-on 0004 (no geometry involved).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0003_address_metadata_route_metadata_routechain_metadata_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='address',
            name='note',
            field=models.TextField(blank=True, help_text='Free-text note about this address.'),
        ),
        migrations.AddField(
            model_name='route',
            name='notes',
            field=models.TextField(blank=True, help_text='Free-text notes about this route.'),
        ),
    ]
