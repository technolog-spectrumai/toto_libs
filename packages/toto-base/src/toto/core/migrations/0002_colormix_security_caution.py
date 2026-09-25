# The security pool's own colour token (cyan) and the caution token every
# theme file already named. Additive: existing rows take the defaults.

import colorfield.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="colormix",
            name="security_light",
            field=colorfield.fields.ColorField(default="#00ACC1", image_field=None, max_length=25, samples=None),
        ),
        migrations.AddField(
            model_name="colormix",
            name="security_dark",
            field=colorfield.fields.ColorField(default="#26C6DA", image_field=None, max_length=25, samples=None),
        ),
        migrations.AddField(
            model_name="colormix",
            name="caution_light",
            field=colorfield.fields.ColorField(default="#A07800", image_field=None, max_length=25, samples=None),
        ),
        migrations.AddField(
            model_name="colormix",
            name="caution_dark",
            field=colorfield.fields.ColorField(default="#F0A820", image_field=None, max_length=25, samples=None),
        ),
    ]
