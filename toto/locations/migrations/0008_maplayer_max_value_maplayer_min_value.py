# Generated manually on 2026-05-09

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("locations", "0007_maplayer_half_range_maplayer_inverted_importance"),
    ]

    operations = [
        migrations.AddField(
            model_name="maplayer",
            name="max_value",
            field=models.FloatField(
                blank=True,
                help_text="Optional display maximum for color scaling.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="maplayer",
            name="min_value",
            field=models.FloatField(
                blank=True,
                help_text="Optional display minimum for color scaling.",
                null=True,
            ),
        ),
    ]
