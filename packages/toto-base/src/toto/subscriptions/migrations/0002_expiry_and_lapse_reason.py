"""An optional end on a subscription, and why the sweep lapsed it (2026-09-26)."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscription",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="subscription",
            name="lapse_reason",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="subscription",
            name="forced",
            field=models.BooleanField(default=False),
        ),
    ]
