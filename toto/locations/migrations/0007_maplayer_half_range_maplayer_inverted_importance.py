# Generated manually on 2026-05-09

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("locations", "0006_merge_20260509_0910"),
    ]

    operations = [
        migrations.AddField(
            model_name="maplayer",
            name="half_range",
            field=models.BooleanField(
                default=False,
                help_text="Use only the low-to-mid half of the color scale.",
            ),
        ),
        migrations.AddField(
            model_name="maplayer",
            name="inverted_importance",
            field=models.BooleanField(
                default=False,
                help_text="Invert value importance before color scaling.",
            ),
        ),
    ]
