"""
Drop the concrete UsageMetric, UsageEvent, UsageQuota tables.

toto.metering is now a pure library app (abstract bases + helpers only).
Each source app owns its own metering tables instead.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("metering", "0001_initial"),
    ]

    operations = [
        # UsageEvent and UsageQuota have FK → UsageMetric (PROTECT),
        # so they must be deleted first.
        migrations.DeleteModel(name="UsageEvent"),
        migrations.DeleteModel(name="UsageQuota"),
        migrations.DeleteModel(name="UsageMetric"),
    ]
